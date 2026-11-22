from fastapi import HTTPException
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from src.config.settings import REDIS_URL
from src.constants.enums import Status
from src.models.entities import Analysis, Audit, Recommendation, Signature, Sleeve, Usage, UsageAmendment
from src.services import weeks

queue = Redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=2)
QUEUE = 'sleeve-demand:notifications'


def record(obj, exclude=()):
    return {col.key: getattr(obj, col.key) for col in obj.__table__.columns if col.key not in exclude}


def audit(db, user, action, subject):
    db.add(Audit(user_id=user.id, action=action, subject=str(subject)))


def save_input(db, model, data, user):
    if model is Usage:
        if not db.scalar(select(Sleeve.id).where(Sleeve.code == data.sleeve_code)):
            raise HTTPException(422, '请选择有效封套规格')
        # 新建周：首次登记值即当前值。补报才走 amend_usage 并触发重算。
        item = Usage(**data.model_dump(), original_state=data.state, original_units=data.units)
        key = f'{data.sleeve_code}/{data.week}'
    else:
        item = model(**data.model_dump())
        key = data.code
    db.add(item)
    audit(db, user, 'create', key)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, '规格编号或领用周已经登记') from exc
    return record(item)


def _notify(analysis_id):
    try:
        queue.lpush(QUEUE, analysis_id)
    except RedisError:
        pass


def _ledger(db, sleeve_code, before, original=False):
    """``before`` 之前的领用周。

    ``original=True`` 取首次登记值，供回测训练“只用各起点当时已知的记录”；
    否则取补报后的当前值，供误差与实领核算。
    """
    query = select(Usage).where(Usage.sleeve_code == sleeve_code, Usage.week < before).order_by(Usage.week)
    rows = []
    for row in db.scalars(query):
        state = row.original_state if original and row.original_state is not None else row.state
        units = row.original_units if original and row.original_state is not None else row.units
        rows.append({'week': row.week.isoformat(), 'state': state, 'units': units})
    return rows


def _training_snapshot(db, sleeve_code, start_week):
    return _ledger(db, sleeve_code, start_week, original=True)


def _actuals(db, sleeve_code, start_week):
    return _ledger(db, sleeve_code, start_week)


def _amendment_ids(db, sleeve_code, before):
    query = (select(UsageAmendment.id)
             .where(UsageAmendment.sleeve_code == sleeve_code, UsageAmendment.week < before)
             .order_by(UsageAmendment.id))
    return list(db.scalars(query))


def amend_usage(db, id, data, user):
    """补报/更正一个领用周，并触发该规格每个采购组重算。

    补报写入补报记录，不改写回测训练用的首次登记值。之后该规格每个采购组
    各排一个新批次；旧批次即使后完成也不会覆盖已经换上的建议。
    """
    row = db.scalar(select(Usage).where(Usage.id == id).with_for_update())
    if row is None:
        raise HTTPException(404, '找不到领用周记录')
    if (row.sleeve_code, row.week) != (data.sleeve_code, data.week):
        raise HTTPException(422, '补报时保留原规格与周日期')
    if (row.state, row.units) == (data.state, data.units):
        return record(row)
    amendment = UsageAmendment(
        usage_id=row.id, sleeve_code=row.sleeve_code, week=row.week,
        old_state=row.state, old_units=row.units, new_state=data.state, new_units=data.units,
        user_id=user.id)
    db.add(amendment)
    row.state, row.units = data.state, data.units
    db.flush()
    audit(db, user, 'amend_week', f'{row.sleeve_code}/{row.week}')
    # 只有起始周晚于该补报周的采购组会把这条领用读入训练/实领；其余组无需重算。
    groups = _affected_groups(db, row.sleeve_code, row.week)
    batches = []
    for group in groups:
        batch = _queue_recompute(db, group, user, triggered_by=[amendment.id])
        batches.append(batch.id)
    db.commit()
    for batch_id in batches:
        _notify(batch_id)
    return record(row)


def _affected_groups(db, sleeve_code, week):
    query = select(Recommendation).where(Recommendation.sleeve_code == sleeve_code,
                                         Recommendation.start_week > week)
    return list(db.scalars(query))


def _queue_recompute(db, group, user, triggered_by):
    group.revision += 1
    group.status = Status.QUEUED
    item = Analysis(
        recommendation_id=group.id, revision=group.revision, sleeve_code=group.sleeve_code,
        title=group.title or group.sleeve_code, status=Status.QUEUED,
        parameters=_parameters(group, group.parameters),
        snapshot=_training_snapshot(db, group.sleeve_code, group.start_week),
        captured_amendments=_amendment_ids(db, group.sleeve_code, group.start_week),
        triggered_by=triggered_by, author_id=user.id)
    db.add(item)
    db.flush()
    return item


def get_input(db, sleeve_code, before=None):
    query = select(Usage).where(Usage.sleeve_code == sleeve_code)
    if before is not None:
        query = query.where(Usage.week < before)
    query = query.order_by(Usage.week)
    return [{**record(row), 'week': row.week.isoformat()} for row in db.scalars(query)]


def _parameters(group, config):
    return {'sleeve_code': group.sleeve_code, 'start_week': group.start_week.isoformat(),
            'alpha': config['alpha'], 'baseline_weeks': config['baseline_weeks'],
            'pack_size': config['pack_size'], 'lead_weeks': config['lead_weeks']}


def _default_start(db, sleeve_code):
    latest = db.scalar(select(func.max(Usage.week)).where(Usage.sleeve_code == sleeve_code))
    if latest is None:
        raise HTTPException(422, '请先录入该规格的领用周')
    return latest + weeks.WEEK


def submit(db, data, user):
    """领单或重算：同一「规格 + 备料起始周」只保留一份现行建议。

    组已存在时只增加批次号并重排一次计算，绝不新建采购单。并发首次提交由
    唯一约束兜底：命中后回读已存在组继续重算，同样不新增采购单。
    """
    sleeve = db.scalar(select(Sleeve).where(Sleeve.code == data.sleeve_code))
    if not sleeve:
        raise HTTPException(422, '请选择有效封套规格')
    start_week = data.start_week or _default_start(db, sleeve.code)
    parameters = {'alpha': data.alpha, 'baseline_weeks': data.baseline_weeks,
                  'pack_size': sleeve.pack_size, 'lead_weeks': sleeve.lead_weeks}

    group = _lock_group(db, sleeve.code, start_week)
    if group is None:
        group = Recommendation(
            sleeve_code=sleeve.code, start_week=start_week, title=data.title, revision=0,
            applied_revision=0, status=Status.QUEUED, parameters=parameters,
            snapshot=[], actuals=[], result=None, covered_amendments=[], warnings=[],
            author_id=user.id)
        db.add(group)
        try:
            db.flush()
        except IntegrityError:
            # 另一个提交刚建了同一组：回读并沿用，不另建单。
            db.rollback()
            group = _lock_group(db, sleeve.code, start_week)
            if group is None:
                raise HTTPException(409, '该规格与起始周的采购单正在创建，请重试') from None
    group.parameters = parameters
    if data.title:
        group.title = data.title
    item = _queue_recompute(db, group, user, triggered_by=[])
    audit(db, user, 'queue', f'{group.id}#{group.revision}')
    db.commit()
    _notify(item.id)
    return _group_detail(db, group)


def _lock_group(db, sleeve_code, start_week):
    return db.scalar(select(Recommendation)
                     .where(Recommendation.sleeve_code == sleeve_code,
                            Recommendation.start_week == start_week)
                     .with_for_update())


def retry(db, id, user):
    """重试既有采购组。只增加批次号，不新增采购单。"""
    group = db.scalar(select(Recommendation).where(Recommendation.id == id).with_for_update())
    if group is None:
        raise HTTPException(404, '找不到采购单')
    item = _queue_recompute(db, group, user, triggered_by=[])
    audit(db, user, 'retry', f'{group.id}#{group.revision}')
    db.commit()
    _notify(item.id)
    return _group_detail(db, group)


def get_group(db, id):
    group = db.get(Recommendation, id)
    if not group:
        raise HTTPException(404, '找不到采购单')
    return group


def get_analysis(db, id):
    item = db.get(Analysis, id)
    if not item:
        raise HTTPException(404, '找不到计算批次')
    return item


def _pending_amendments(db, group):
    """当前生效建议之后才补报、还没算进去的星期（限于该组训练/实领窗口）。"""
    covered = set(group.covered_amendments or [])
    query = (select(UsageAmendment)
             .where(UsageAmendment.sleeve_code == group.sleeve_code,
                    UsageAmendment.week < group.start_week)
             .order_by(UsageAmendment.id))
    return [row.week.isoformat() for row in db.scalars(query) if row.id not in covered]


def _signature_record(db, group):
    signature = db.scalar(select(Signature).where(Signature.recommendation_id == group.id)
                          .order_by(Signature.id.desc()).limit(1))
    if not signature:
        return None
    return {'revision': signature.revision, 'packs': signature.packs, 'result': signature.result,
            'parameters': signature.parameters, 'snapshot': signature.snapshot,
            'actuals': signature.actuals, 'warnings': signature.warnings,
            'note': signature.note, 'signed_at': weeks.iso(signature.signed_at),
            'signed_by': signature.signed_by,
            'is_current': signature.revision == group.applied_revision}


def _group_detail(db, group):
    data = record(group, ('snapshot', 'actuals'))
    data['start_week'] = group.start_week.isoformat()
    data['pending_amendments'] = _pending_amendments(db, group)
    data['signature'] = _signature_record(db, group)
    data['history'] = [
        record(item, ('snapshot', 'actuals')) | {'created_at': weeks.iso(item.created_at)}
        for item in db.scalars(select(Analysis).where(Analysis.recommendation_id == group.id)
                               .order_by(Analysis.id.desc()).limit(20))
    ]
    return data


def list_groups(db):
    return [_group_detail(db, group)
            for group in db.scalars(select(Recommendation).order_by(Recommendation.id.desc()))]


def publish(db, id, note, user):
    """签核冻结一份建议：按当时的快照、实领和包数留档回查。"""
    group = db.scalar(select(Recommendation).where(Recommendation.id == id).with_for_update())
    if not group:
        raise HTTPException(404, '找不到采购单')
    if group.status not in (Status.COMPLETED, Status.STALE):
        raise HTTPException(409, '只有已有现行建议的采购单可签核')
    if not group.result:
        raise HTTPException(409, '采购单尚无可签核的建议')
    packs = group.result.get('metrics', {}).get('建议备料（包）')
    latest = db.scalar(select(Analysis).where(Analysis.recommendation_id == group.id,
                                              Analysis.revision == group.applied_revision))
    signature = Signature(
        recommendation_id=group.id, revision=group.applied_revision,
        analysis_id=latest.id if latest else None,
        sleeve_code=group.sleeve_code, start_week=group.start_week,
        parameters=dict(group.parameters), snapshot=group.snapshot, actuals=group.actuals,
        result=group.result, packs=packs, warnings=group.warnings or [], note=note,
        signed_by=user.id)
    db.add(signature)
    group.status = Status.PUBLISHED
    audit(db, user, 'publish', f'{group.id}#{group.applied_revision}')
    db.commit()
    return _group_detail(db, group)


def current(db, group):
    """页面和下载共用：返回现行建议（采购组上锁定的 result）与最新批次记录。"""
    latest = db.scalar(select(Analysis).where(Analysis.recommendation_id == group.id)
                       .order_by(Analysis.revision.desc()).limit(1))
    return {'group': group, 'latest': latest, 'signature': _signature_record(db, group)}

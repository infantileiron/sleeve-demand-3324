import json
import logging
import time
from pathlib import Path

from redis.exceptions import RedisError
from sqlalchemy import select, update

from src.config.database import Session
from src.constants.enums import Status
from src.models.entities import Analysis, Recommendation, UsageAmendment
from src.services.business import QUEUE, _actuals, _amendment_ids, queue
from src.services.forecast import analyze


def next_job():
    with Session() as db:
        item = db.scalar(select(Analysis).where(Analysis.status == Status.QUEUED)
                         .order_by(Analysis.id).limit(1).with_for_update(skip_locked=True))
        if not item:
            return False
        item.status = Status.RUNNING
        db.commit()
        group = db.get(Recommendation, item.recommendation_id) if item.recommendation_id else None
        try:
            # 训练输入固定为排入时锁定的快照；实领取补报后的最新记录。
            # 在同一刻读取实领和补报编号，准确记录本批次算了哪些补报。
            current = _actuals(db, group.sleeve_code, group.start_week) if group else item.snapshot
            if group:
                item.captured_amendments = _amendment_ids(db, group.sleeve_code, group.start_week)
            item.actuals = current
            result = analyze(item.snapshot, item.parameters, actuals=current)
            try:
                artifact = Path('/artifacts')
                artifact.mkdir(parents=True, exist_ok=True)
                temp = artifact / f'.sleeve-{item.id}.tmp'
                temp.write_text(json.dumps(result, ensure_ascii=False))
                temp.replace(artifact / f'sleeve-{item.id}.json')
            except OSError:
                # 证据 JSON 是附加产物，写不出不应让建议失败。
                logging.exception('Artifact write failed for analysis %s', item.id)
            item.result = result
            item.status = Status.COMPLETED
            db.commit()
            if group:
                complete(db, group, item)
        except ValueError as exc:
            item.note = str(exc)
            item.status = Status.FAILED
            db.commit()
            if group:
                complete(db, group, item, failure=str(exc))
        except Exception:
            logging.exception('Analysis %s failed', item.id)
            item.note = '后台计算失败，请检查数据后重提'
            item.status = Status.FAILED
            db.commit()
            if group:
                complete(db, group, item, failure=item.note)
        return True


def _covered(group, upto):
    return set(group.covered_amendments or []) | set(upto or [])


def complete(db, group, item, failure=None):
    """把一次批次的结果并入采购组的现行建议。

    - 成功批次只在比已采用批次更新时替换建议；迟到的旧批次保留证据，绝不
      覆盖已经换上的建议（两次补报可能乱序完成）。
    - 失败批次只有在它仍是该组最新请求（``revision == group.revision``）时
      才处理：沿用上次可用建议，并标明哪些补报没算进去；被更新请求取代的
      失败不改变现行建议。
    """
    db.refresh(group)
    if item.status == Status.COMPLETED and item.result:
        if item.revision <= group.applied_revision:
            # 迟到的旧批次：保留本批次记录，但不改现行建议。
            return
        covered = _covered(group, item.captured_amendments)
        missed = [row.week.isoformat() for row in _amendments(db, group.sleeve_code, group.start_week)
                  if row.id not in covered]
        group.applied_revision = item.revision
        group.result = item.result
        group.snapshot = item.snapshot
        group.actuals = item.actuals or []
        group.covered_amendments = sorted(covered)
        group.note = ''
        # 若已有更新请求且已经失败，则这是「沿用上次可用」，不是全新完成。
        newer_failed = db.scalar(
            select(Analysis.revision).where(
                Analysis.recommendation_id == group.id,
                Analysis.revision > item.revision,
                Analysis.status == Status.FAILED).order_by(Analysis.revision.desc()).limit(1))
        if newer_failed:
            group.status = Status.STALE
            group.warnings = [f'批次 {newer_failed} 算不出，沿用批次 {item.revision} 的可用建议']
            group.warnings += [f'{week} 的补报未纳入本次计算' for week in missed]
        else:
            group.status = Status.STALE if missed else Status.COMPLETED
            group.warnings = [f'{week} 的补报未纳入本次计算' for week in missed]
    else:
        if item.revision != group.revision:
            # 已被更新的请求取代：失败不改变现行建议。
            return
        if group.result:
            # 最新一批算不出：沿用上次可用建议。未纳入范围以现行建议为准，
            # 即现行建议之后新到的补报都算没算进去。
            applied = set(group.covered_amendments or [])
            missed = [row.week.isoformat() for row in _amendments(db, group.sleeve_code, group.start_week)
                      if row.id not in applied]
            group.status = Status.STALE
            ref = f'（批次 {item.revision}：{failure or item.note}）'
            group.warnings = [f'最新一批算不出{ref}，沿用上次可用建议']
            group.warnings += [f'{week} 的补报未纳入本建议' for week in missed]
        else:
            group.status = Status.FAILED
            group.note = failure or item.note
            missed = [row.week.isoformat() for row in _amendments(db, group.sleeve_code, group.start_week)]
            group.warnings = [f'{week} 的补报未纳入计算' for week in missed]
    db.commit()


def _amendments(db, sleeve_code, before):
    query = (select(UsageAmendment)
             .where(UsageAmendment.sleeve_code == sleeve_code, UsageAmendment.week < before)
             .order_by(UsageAmendment.id))
    return list(db.scalars(query))


def main():
    with Session() as db:
        # 中断的批次重新排入，不新建采购单，沿用其锁定的快照。
        db.execute(update(Analysis).where(Analysis.status == Status.RUNNING).values(status=Status.QUEUED))
        db.commit()
    while True:
        Path('/tmp/worker-heartbeat').touch()
        if next_job():
            continue
        try:
            queue.blpop(QUEUE, timeout=2)
        except RedisError:
            time.sleep(2)


if __name__ == '__main__':
    main()

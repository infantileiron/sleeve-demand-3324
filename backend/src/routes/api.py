from fastapi import APIRouter, Depends
from sqlalchemy import select

from src.config.database import get_db
from src.controllers.auth import current, login, manager, writable
from src.controllers.export import export
from src.models.entities import Audit, Sleeve, Usage
from src.schemas.business import AnalysisInput, Login, Publication, SleeveInput, UsageInput
from src.services import business as svc

router = APIRouter(prefix='/api')


@router.post('/auth/login')
def sign_in(data: Login, db=Depends(get_db)):
    return login(data, db)


@router.get('/auth/me')
def me(user=Depends(current)):
    return {'email': user.email, 'role': user.role}


@router.get('/sleeves')
def sleeves(db=Depends(get_db), user=Depends(current)):
    return [svc.record(item) for item in db.scalars(select(Sleeve).order_by(Sleeve.code))]


@router.post('/sleeves', status_code=201)
def create_sleeve(data: SleeveInput, db=Depends(get_db), user=Depends(writable)):
    return svc.save_input(db, Sleeve, data, user)


@router.get('/usage')
def usage(sleeve_code: str | None = None, db=Depends(get_db), user=Depends(current)):
    query = select(Usage).order_by(Usage.week.desc(), Usage.id.desc())
    if sleeve_code:
        query = query.where(Usage.sleeve_code == sleeve_code)
    return [svc.record(item) for item in db.scalars(query)]


@router.post('/usage', status_code=201)
def create_usage(data: UsageInput, db=Depends(get_db), user=Depends(writable)):
    return svc.save_input(db, Usage, data, user)


@router.post('/usage/{id}/amend')
def amend(id: int, data: UsageInput, db=Depends(get_db), user=Depends(writable)):
    return svc.amend_usage(db, id, data, user)


# 采购组：一份现行建议 = 「规格 + 备料起始周」。页面和下载都读这里。
@router.get('/recommendations')
def recommendations(db=Depends(get_db), user=Depends(current)):
    return svc.list_groups(db)


@router.post('/recommendations', status_code=202)
def enqueue(data: AnalysisInput, db=Depends(get_db), user=Depends(writable)):
    return svc.submit(db, data, user)


@router.get('/recommendations/{id}')
def recommendation(id: int, db=Depends(get_db), user=Depends(current)):
    return svc._group_detail(db, svc.get_group(db, id))


@router.get('/recommendations/{id}/export')
def download(id: int, db=Depends(get_db), user=Depends(current)):
    bundle = svc.current(db, svc.get_group(db, id))
    return export(bundle['group'], bundle['latest'], bundle['signature'])


@router.post('/recommendations/{id}/retry', status_code=202)
def retry(id: int, db=Depends(get_db), user=Depends(writable)):
    return svc.retry(db, id, user)


@router.post('/recommendations/{id}/publish')
def publish(id: int, data: Publication, db=Depends(get_db), user=Depends(manager)):
    return svc.publish(db, id, data.note, user)


# 批次详情：保留失败证据和每批锁定快照，供回查。
@router.get('/batches/{id}')
def batch(id: int, db=Depends(get_db), user=Depends(current)):
    return svc.record(svc.get_analysis(db, id))


@router.get('/audits')
def audit_log(db=Depends(get_db), user=Depends(manager)):
    return [svc.record(item) for item in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(100))]

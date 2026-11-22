from datetime import date, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from src.config.database import Base


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(120), unique=True)
    password_hash: Mapped[str] = mapped_column(String(150))
    role: Mapped[str] = mapped_column(String(16))


class Sleeve(Base):
    __tablename__ = 'sleeves'
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    title: Mapped[str] = mapped_column(String(100))
    pack_size: Mapped[int]
    lead_weeks: Mapped[int]


class Usage(Base):
    __tablename__ = 'weekly_usage'
    __table_args__ = (UniqueConstraint('sleeve_code', 'week', name='uq_usage_sleeve_week'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sleeve_code: Mapped[str] = mapped_column(ForeignKey('sleeves.code'))
    week: Mapped[date] = mapped_column(Date)
    state: Mapped[str] = mapped_column(String(12))
    units: Mapped[int | None]
    # 首次登记时的值。补报只改 ``state/units``，这两列保持首次登记内容，
    # 让回测训练能做到“只用各起点当时已知的记录”。新建行时与当前值相同。
    original_state: Mapped[str | None] = mapped_column(String(12))
    original_units: Mapped[int | None]


class UsageAmendment(Base):
    """一次补报/更正的可追溯记录。

    补报不会改写历史回测的训练输入；它只触发新的重算批次。批次是否把某条
    补报算进去，由 ``Analysis.captured_amendments`` 与采购组当前生效批次的
    ``covered_amendments`` 对照得出。
    """

    __tablename__ = 'usage_amendments'
    id: Mapped[int] = mapped_column(primary_key=True)
    usage_id: Mapped[int] = mapped_column(ForeignKey('weekly_usage.id'))
    sleeve_code: Mapped[str] = mapped_column(ForeignKey('sleeves.code'), index=True)
    week: Mapped[date] = mapped_column(Date)
    old_state: Mapped[str] = mapped_column(String(12))
    old_units: Mapped[int | None]
    new_state: Mapped[str] = mapped_column(String(12))
    new_units: Mapped[int | None]
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Recommendation(Base):
    """采购组：按「规格 + 备料起始周」唯一，只认一份现行建议。

    ``revision`` 是最近一次请求的批次号，``applied_revision`` 是当前结果
    来自哪一批次。后完成的旧批次不得覆盖 ``result``。
    """

    __tablename__ = 'recommendations'
    __table_args__ = (UniqueConstraint('sleeve_code', 'start_week', name='uq_recommendation_group'),)
    id: Mapped[int] = mapped_column(primary_key=True)
    sleeve_code: Mapped[str] = mapped_column(ForeignKey('sleeves.code'), index=True)
    start_week: Mapped[date] = mapped_column(Date)
    title: Mapped[str] = mapped_column(String(100), default='')
    revision: Mapped[int] = mapped_column(default=0)
    applied_revision: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(16), index=True)
    parameters: Mapped[dict] = mapped_column(JSON)
    # 当前生效建议锁定的训练快照（各回测起点当时已知的记录）。
    snapshot: Mapped[list] = mapped_column(JSON, default=list)
    # 计算时读到的补报后实领，用于误差与本批次回查。
    actuals: Mapped[list] = mapped_column(JSON, default=list)
    result: Mapped[dict | None] = mapped_column(JSON)
    covered_amendments: Mapped[list] = mapped_column(JSON, default=list)
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default='')
    author_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Analysis(Base):
    """一次计算批次。保留每个批次的锁定快照、实领和结果，供回查与失败证据。"""

    __tablename__ = 'forecast_sheets'
    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int | None] = mapped_column(ForeignKey('recommendations.id'), index=True)
    revision: Mapped[int | None]
    sleeve_code: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), index=True)
    parameters: Mapped[dict] = mapped_column(JSON)
    # 提交/重算时锁定的训练记录，之后不再被补报改写。
    snapshot: Mapped[list] = mapped_column(JSON)
    # 本批次被触发时已经存在的补报编号，用于判断哪些补报没有算进去。
    captured_amendments: Mapped[list] = mapped_column(JSON, default=list)
    triggered_by: Mapped[list] = mapped_column(JSON, default=list)
    # 计算收尾时读到的补报后实领。
    actuals: Mapped[list | None] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    author_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    note: Mapped[str] = mapped_column(Text, default='')
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Signature(Base):
    """签核时冻结的一份建议，按当时的数据和包数回查，不随后续补报变化。"""

    __tablename__ = 'signatures'
    id: Mapped[int] = mapped_column(primary_key=True)
    recommendation_id: Mapped[int] = mapped_column(ForeignKey('recommendations.id'), index=True)
    revision: Mapped[int]
    analysis_id: Mapped[int | None] = mapped_column(ForeignKey('forecast_sheets.id'))
    sleeve_code: Mapped[str] = mapped_column(String(32))
    start_week: Mapped[date] = mapped_column(Date)
    parameters: Mapped[dict] = mapped_column(JSON)
    snapshot: Mapped[list] = mapped_column(JSON)
    actuals: Mapped[list] = mapped_column(JSON)
    result: Mapped[dict] = mapped_column(JSON)
    packs: Mapped[int]
    warnings: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str] = mapped_column(Text, default='')
    signed_by: Mapped[int] = mapped_column(ForeignKey('users.id'))
    signed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class Audit(Base):
    __tablename__ = 'audits'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    action: Mapped[str] = mapped_column(String(40))
    subject: Mapped[str] = mapped_column(String(100))
    recorded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

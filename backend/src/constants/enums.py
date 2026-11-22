from enum import StrEnum


class Role(StrEnum):
    MANAGER = 'manager'
    ANALYST = 'analyst'
    VIEWER = 'viewer'


class Status(StrEnum):
    QUEUED = 'queued'
    RUNNING = 'running'
    COMPLETED = 'completed'
    FAILED = 'failed'
    PUBLISHED = 'published'
    # 最新批次算不出，沿用上一次可用建议；仍有补报未纳入。
    STALE = 'stale'

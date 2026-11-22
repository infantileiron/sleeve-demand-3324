from datetime import timedelta

WEEK = timedelta(days=7)


def iso(value):
    return value.isoformat() if value else None

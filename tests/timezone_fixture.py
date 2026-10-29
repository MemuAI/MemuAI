"""Deterministic local datetime for timezone tests without changing Windows settings."""
from datetime import datetime
from zoneinfo import ZoneInfo


def local_datetime(name):
    zone = ZoneInfo(name)

    class LocalDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            value = super().now(tz or zone)
            return value if tz is not None else value.replace(tzinfo=None)

        @classmethod
        def fromtimestamp(cls, timestamp, tz=None):
            value = super().fromtimestamp(timestamp, tz or zone)
            return value if tz is not None else value.replace(tzinfo=None)

        def astimezone(self, tz=None):
            value = self if self.tzinfo is not None else self.replace(tzinfo=zone)
            return datetime.astimezone(value, tz or zone)

        def timestamp(self):
            value = self if self.tzinfo is not None else self.replace(tzinfo=zone)
            return datetime.timestamp(value)

    return LocalDateTime

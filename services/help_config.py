import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DonorLimitConfig:
    account_actor_limit: int
    account_ip_limit: int
    account_window_seconds: int
    report_limit: int
    report_window_seconds: int


@dataclass(frozen=True)
class TemporaryAccessLimitConfig:
    email_limit: int
    ip_limit: int
    window_seconds: int
    retention_days: int


def _positive_int(name, default, *, maximum):
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1 or value > maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return value


def donor_limit_config():
    return DonorLimitConfig(
        account_actor_limit=_positive_int("DONOR_ACCOUNT_ACTOR_LIMIT", 10, maximum=1000),
        account_ip_limit=_positive_int("DONOR_ACCOUNT_IP_LIMIT", 30, maximum=1000),
        account_window_seconds=_positive_int(
            "DONOR_ACCOUNT_LIMIT_WINDOW_SECONDS", 300, maximum=86400
        ),
        report_limit=_positive_int("DONOR_REPORT_LIMIT", 20, maximum=1000),
        report_window_seconds=_positive_int(
            "DONOR_REPORT_LIMIT_WINDOW_SECONDS", 3600, maximum=86400
        ),
    )


def temporary_access_limit_config():
    return TemporaryAccessLimitConfig(
        email_limit=_positive_int("TEMPORARY_ACCESS_EMAIL_LIMIT", 3, maximum=1000),
        ip_limit=_positive_int("TEMPORARY_ACCESS_IP_LIMIT", 10, maximum=1000),
        window_seconds=_positive_int(
            "TEMPORARY_ACCESS_LIMIT_WINDOW_SECONDS", 15 * 60, maximum=86400
        ),
        retention_days=_positive_int(
            "TEMPORARY_ACCESS_ATTEMPT_RETENTION_DAYS", 30, maximum=365
        ),
    )

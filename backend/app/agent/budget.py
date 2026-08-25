from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.config import get_settings
from app.db.models import UsageCounter
from app.db.session import get_sessionmaker

_PER_USER_HOURLY_REQUEST_CAP = 30
_PER_USER_DAILY_TOKEN_CAP = 200_000


class AbortRun(Exception):
    """Raised when a per-conversation or per-user cap is breached. The loop
    catches this, ends the turn with a clear message, and finalizes the run
    with RunStatus.ABORTED -- never a silent truncation (spec 12.3)."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass
class TurnBudget:
    """Per-conversation-turn resource cap (spec 12.3): 12 tool iterations,
    $0.50 cumulative cost, 60s wall clock, by default."""

    max_iterations: int
    max_cost_usd: Decimal
    max_wall_seconds: float
    started_at: float = field(default_factory=time.monotonic)
    iterations: int = 0
    cost_usd: Decimal = Decimal("0")

    def check(self) -> None:
        if self.iterations >= self.max_iterations:
            raise AbortRun(f"exceeded {self.max_iterations} tool iterations")
        if self.cost_usd >= self.max_cost_usd:
            raise AbortRun(f"exceeded ${self.max_cost_usd} conversation budget")
        if time.monotonic() - self.started_at >= self.max_wall_seconds:
            raise AbortRun(f"exceeded {self.max_wall_seconds}s wall clock")

    def record_iteration(self, cost: Decimal | None) -> None:
        self.iterations += 1
        if cost is not None:
            self.cost_usd += cost


def new_turn_budget() -> TurnBudget:
    settings = get_settings()
    return TurnBudget(
        max_iterations=settings.max_tool_iterations,
        max_cost_usd=Decimal(str(settings.max_cost_per_conversation_usd)),
        max_wall_seconds=60,
    )


def agent_enabled() -> bool:
    return get_settings().agent_enabled


def _hour_bucket(now: datetime) -> datetime:
    return now.replace(minute=0, second=0, microsecond=0)


def check_and_record_usage(user_key: str, *, tokens: int, cost: Decimal) -> None:
    """Enforces spec 12.3's per-user caps (30 requests/hour, 200k tokens/day)
    and records this call's usage. Uses UsageCounter's existing
    (user_key, window_start) schema with an HOURLY window_start for every
    row -- there's no separate daily-window column, so the daily cap is
    computed by summing every row for this user_key in the last 24h rather
    than adding a new column. Raises AbortRun *before* recording if either
    cap would be breached by this call, so a rejected call doesn't count
    against the user's own budget."""
    Session = get_sessionmaker()
    now = datetime.now(timezone.utc)
    bucket = _hour_bucket(now)
    day_ago = now - timedelta(hours=24)

    with Session() as session:
        current_hour = session.get(UsageCounter, (user_key, bucket))
        current_requests = current_hour.requests if current_hour else 0
        if current_requests >= _PER_USER_HOURLY_REQUEST_CAP:
            raise AbortRun(f"exceeded {_PER_USER_HOURLY_REQUEST_CAP} requests/hour")

        day_rows = (
            session.query(UsageCounter)
            .filter(UsageCounter.user_key == user_key, UsageCounter.window_start >= day_ago)
            .all()
        )
        day_tokens = sum(row.input_tokens + row.output_tokens for row in day_rows)
        if day_tokens + tokens > _PER_USER_DAILY_TOKEN_CAP:
            raise AbortRun(f"exceeded {_PER_USER_DAILY_TOKEN_CAP} tokens/day")

        if current_hour is None:
            current_hour = UsageCounter(
                user_key=user_key, window_start=bucket, requests=0, input_tokens=0, output_tokens=0, cost_usd=Decimal("0"),
            )
            session.add(current_hour)
        current_hour.requests += 1
        current_hour.input_tokens += tokens
        current_hour.cost_usd += cost
        session.commit()

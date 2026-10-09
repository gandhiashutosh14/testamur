"""Per-tenant daily spend gates for the paid steps (OCR pages and model tokens).

A gate is checked before money is spent, never after, and the check *reserves* what it allows in the same
transaction: two workers checking at the same moment cannot both spend the last of a budget (the first version
let that happen; the load test caught it). The reservation is settled to the actual amount afterwards, or released
if the work fails. Cache hits are free and always allowed.

A tenant over its cap gets a BudgetExceeded carrying the time the budget resets; the worker defers the job to then.
Nothing is dropped, and a status is never computed from a half-finished job: the whole job waits."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .store import TenantBudget, UsageRow


class BudgetExceeded(Exception):
    def __init__(self, tenant_id: int, provider: str, used: float, cap: int, wanted: float, retry_at: datetime):
        super().__init__(f"tenant {tenant_id} would exceed its daily {provider} budget: {used:.0f} used + "
                         f"{wanted:.0f} wanted > {cap}")
        self.tenant_id, self.provider, self.used, self.cap, self.wanted, self.retry_at = \
            tenant_id, provider, used, cap, wanted, retry_at


def _cap(session: Session, tenant_id: int, provider: str) -> int | None:
    budget = session.get(TenantBudget, tenant_id)
    if budget is None:
        return None
    return budget.ocr_pages_per_day if provider == "ocr" else budget.model_tokens_per_day


def _used(session: Session, provider: str, today: date) -> float:
    return session.scalar(select(func.coalesce(func.sum(UsageRow.units), 0.0)).where(
        UsageRow.provider == provider, UsageRow.cached.is_(False), UsageRow.day == today)) or 0.0


def check(session: Session, tenant_id: int, provider: str, wanted: float, today: date) -> None:
    """Refuse if `wanted` would take the tenant over today's cap. For spends recorded in the same transaction."""
    cap = _cap(session, tenant_id, provider)
    if cap is None:
        return
    used = _used(session, provider, today)
    if used + wanted > cap:
        raise BudgetExceeded(tenant_id, provider, used, cap, wanted,
                             retry_at=datetime.combine(today + timedelta(days=1), time(0, 0)))


def reserve(session: Session, tenant_id: int, provider: str, amount: float, today: date) -> UsageRow:
    """Check and reserve in one transaction. Settle the returned row to the actual amount, or delete it to release."""
    check(session, tenant_id, provider, amount, today)
    row = UsageRow(day=today, provider=provider, units=amount, cached=False)
    session.add(row)
    session.flush()
    return row

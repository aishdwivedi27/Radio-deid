"""Open the store and reconcile the output at startup (SPEC §5.3 step 7, §8 reliability). TR-REL-NF-01."""

from __future__ import annotations

from app.config import Settings
from app.services.context import ServiceContext
from app.services.records.reconcile import ReconcileReport, reconcile


def startup(settings: Settings) -> ServiceContext:
    ctx = ServiceContext.from_settings(settings)
    reconcile(ctx)
    return ctx


def run_reconcile(settings: Settings) -> ReconcileReport:
    ctx = ServiceContext.from_settings(settings)
    try:
        return reconcile(ctx)
    finally:
        ctx.db.dispose()

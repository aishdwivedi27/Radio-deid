"""Open the store and reconcile the output at startup (SPEC §5.3 step 7, §8 reliability). TR-REL-NF-01.

``install_log_filter`` puts the identifier filter in front of every logger (SPEC §7, TR-SEC-02).
"""

from __future__ import annotations

from app.config import Settings
from app.deid import logsafe
from app.services.context import ServiceContext
from app.services.records.reconcile import ReconcileReport, reconcile


def install_log_filter() -> None:
    logsafe.install()


def startup(settings: Settings) -> ServiceContext:
    install_log_filter()
    ctx = ServiceContext.from_settings(settings)
    reconcile(ctx)
    return ctx


def run_reconcile(settings: Settings) -> ReconcileReport:
    install_log_filter()
    ctx = ServiceContext.from_settings(settings)
    try:
        return reconcile(ctx)
    finally:
        ctx.db.dispose()

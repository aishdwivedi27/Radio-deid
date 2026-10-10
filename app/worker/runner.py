"""The job worker: one background thread, one job and one study at a time (SPEC §1, §6.5, §8).
TR-ING-01, TR-REL-NF-01.

It holds a cross-process lock while it runs, so only one worker exists per data root. On start it recovers
(interrupted uploads failed, interrupted jobs queued to resume), then polls the jobs table. ``stop`` lets the
current study finish; the job then stays ``processing`` and resumes at the next start.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

from filelock import Timeout

from app.services.context import ServiceContext
from app.services.jobs import create, run

log = logging.getLogger(__name__)
POLL_S = 0.5
LOCK_RETRY_S = 5.0


class Worker:
    def __init__(self, ctx: ServiceContext, poll: float = POLL_S) -> None:
        self.ctx, self.poll = ctx, poll
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._main, name="deid-worker", daemon=True)

    def start(self) -> Worker:
        self._thread.start()
        return self

    def stop(self, timeout: float = 600.0) -> None:
        self._stop.set()
        self._thread.join(timeout)

    def run_pending(self) -> int:
        """Run every queued job now, in this thread (tests and the CLI). Returns the number run."""
        n = 0
        while not self._stop.is_set() and (job_id := create.claim_next(self.ctx)) is not None:
            run.run_job(self.ctx, job_id, self._stop.is_set)
            n += 1
        return n

    def _main(self) -> None:
        lock = create.worker_lock(self.ctx)
        while not self._stop.is_set():
            try:
                lock.acquire()
                break
            except Timeout:
                self._stop.wait(LOCK_RETRY_S)  # another instance holds the worker
        else:
            return
        try:
            create.recover(self.ctx)
            while not self._stop.is_set():
                try:
                    ran = self.run_pending()
                except Exception as exc:  # noqa: BLE001 - keep the worker alive; type name only
                    log.error("worker error (%s)", type(exc).__name__)
                    ran = 0
                if not ran:
                    self._stop.wait(self.poll)
        finally:
            lock.release()


def start_worker(ctx: ServiceContext) -> Callable[[], None]:
    worker = Worker(ctx).start()
    return worker.stop

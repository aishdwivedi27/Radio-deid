"""Demo of the Phase 2 store: ingest an inbox, approve and finalise every record, then re-run the inbox.

    python tools/demo_finalise.py <data_root> <inbox>

Synthetic data only: no ethics approval is recorded, so pre-approval mode refuses anything that is not
SYNTHETIC-DEMO / DEMO- (SPEC §4.1). Prints de-identification numbers, statuses and counts only.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.deid.index import index_input  # noqa: E402
from app.deid.keys import load_or_create_key  # noqa: E402
from app.deid.report.match import match_reports  # noqa: E402
from app.deid.types import DeidSettings  # noqa: E402
from app.services.context import ServiceContext  # noqa: E402
from app.services.records.approve import record_decision  # noqa: E402
from app.services.records.finalise import finalise  # noqa: E402
from app.services.records.ingest import ingest_study  # noqa: E402

REVIEWER, CATEGORY = "u_demo", "NORMAL"


def main(root: Path, inbox: Path) -> int:
    ctx = ServiceContext.open(root / "output", root / "app_data")
    key = load_or_create_key(root / "app_data" / "secret.key")
    index = index_input(inbox)
    matches = match_reports(index)
    try:
        for i, group in enumerate(index.studies):
            res = ingest_study(ctx, group, matches.by_study.get(i), key, DeidSettings(job_id="J-DEMO"))
            print(res.record_id, res.status, f"v{res.version}")
            if res.status == "awaiting_review" and res.version:
                record_decision(ctx, res.record_id, res.version, REVIEWER, "approved", CATEGORY)
                fin = finalise(ctx, res.record_id, res.version, REVIEWER, CATEGORY)
                print("  ->", fin.status, f"new image rows={len(fin.image_rows)}")
        again = [
            ingest_study(ctx, g, matches.by_study.get(i), key, DeidSettings(job_id="J-DEMO2")).status
            for i, g in enumerate(index.studies)
        ]
        print("re-run of the same inbox:", again)
    finally:
        ctx.db.dispose()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(Path(sys.argv[1]), Path(sys.argv[2])))

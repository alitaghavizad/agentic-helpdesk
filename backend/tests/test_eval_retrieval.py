import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import eval_retrieval  # noqa: E402


@pytest.fixture(autouse=True)
def _cleanup_ingest_eval_runs():
    """eval_retrieval.run_eval() commits a real Run(trigger=INGEST_EVAL) via
    start_run()/end_run() on every call -- there is no stuck-RUNNING bug
    (the script brackets both correctly), but nothing ever deletes the
    finalized row. This file's recall-retry loop can call run_eval() up to
    twice per test; left unswept, this accumulates permanently in the
    shared dev Postgres runs table. Same before/after started_at range
    pattern used repeatedly in the phase 9 learning-loop work for the
    identical leak class."""
    from app.db.models import Run, RunTrigger, Span
    from app.db.session import get_sessionmaker

    Session = get_sessionmaker()
    with Session() as s:
        before = (
            s.query(Run.started_at).filter(Run.trigger == RunTrigger.INGEST_EVAL)
            .order_by(Run.started_at.desc()).first()
        )

    yield

    with Session() as s:
        query = s.query(Run.id).filter(Run.trigger == RunTrigger.INGEST_EVAL)
        if before is not None:
            query = query.filter(Run.started_at > before[0])
        run_ids = [r[0] for r in query.all()]
        if run_ids:
            s.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
            s.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
            s.commit()

# The design spec targets Recall@5 >= 0.70 (see docs/superpowers/specs/... section 7.3),
# and scripts/eval_retrieval.py's RECALL_5_GATE stays at 0.7 on purpose -- `make eval` is
# meant to keep reporting an honest signal against that real design target.
#
# For the pytest suite, though, the achieved number against this dataset is 0.6958,
# accepted as documented in docs/superpowers/plans/2026-08-25-rag-ingestion-retrieval.md's
# Post-implementation note section: a real chunking bug (boilerplate "Support context" text producing
# semantic ties across unrelated employee documents) was found and fixed via identity-
# prefixed chunk text, closing most of the gap (0.6854 -> 0.6958). The remainder was
# diagnosed as an eval-dataset artifact, not a retrieval defect: ~7 "which employees use
# <tool>" enumeration queries have ground truth capped at a small, arbitrary sample of a
# much larger valid-answer set. For example Q047 ("Which employees use Slack?") lists only
# 6 relevant docs, but 85 of the 100 employees actually have Slack listed in their tools,
# with no content-based signal in the corpus that would let retrieval distinguish the
# "chosen" 6 from the other 79 equally-valid matches. This was verified directly against
# the live dataset and independently reproduced twice.
#
# 2026-08-27: the floor is now the spec's real 0.70. The old 0.69 was set below the then-
# achieved 0.6958, and that gap turned out to be load-bearing in the wrong direction: it let
# a genuinely regressed index (a re-ingest over a pre-filter collection reconstitutes the old
# corpus and measures exactly 0.6958) pass as green. The remaining boilerplate was then
# removed at ingestion -- see app.rag.chunking.drop_nondiscriminating_chunks -- taking a
# clean rebuild to 0.7125, so the honest spec threshold is now achievable and is what we
# assert. Headroom is genuinely thin (~0.01), which is deliberate: this number sitting just
# above its gate is information, and burying it under a lower floor is exactly how the
# earlier regression stayed invisible for two build phases.
ACCEPTED_RECALL_5_FLOOR = 0.70

# When this test runs as part of the full suite (not in isolation), it lands right after
# several other tests (test_rag_direct_backend.py, test_rag_mcp_backend.py,
# test_rag_backend_equivalence.py, test_ingest_dataset.py) that create, query, and delete
# Chroma collections in quick succession, and test_ingest_dataset.py performs two full
# in-place re-upserts of both shared collections. A query issued immediately after that
# churn can read a briefly-perturbed index, so one retry after a short wait is kept.
#
# 2026-08-27 correction: the churn was previously blamed on "transient read-after-write
# consistency lag", and that explanation was doing too much work -- it was also used to wave
# away readings that were in fact a real, reproducible quality regression. Two concrete
# contributors have since been found and fixed: test_rag_mcp_backend.py leaked a Chroma
# collection on every run (45 had accumulated), and template chunks identical across every
# document were producing near-tied rankings decided by noise. Treat a failure here as real
# until measured otherwise -- do not re-label it a flake without re-running the eval and
# reading the actual number.
RECALL_5_RETRY_WAIT_SECONDS = 5
RECALL_5_MAX_ATTEMPTS = 2


async def test_retrieval_recall_at_5_meets_accepted_floor():
    summary = None
    for attempt in range(1, RECALL_5_MAX_ATTEMPTS + 1):
        summary = await eval_retrieval.run_eval()
        assert summary["n"] == 60
        if summary["recall@5"] >= ACCEPTED_RECALL_5_FLOOR:
            return
        if attempt < RECALL_5_MAX_ATTEMPTS:
            await asyncio.sleep(RECALL_5_RETRY_WAIT_SECONDS)

    worst_five = sorted(summary["per_query"], key=lambda r: r["recall@5"])[:5]
    assert summary["recall@5"] >= ACCEPTED_RECALL_5_FLOOR, (
        f"Recall@5 = {summary['recall@5']:.4f} is below the accepted {ACCEPTED_RECALL_5_FLOOR} "
        f"floor after {RECALL_5_MAX_ATTEMPTS} attempts ({RECALL_5_RETRY_WAIT_SECONDS}s apart); "
        f"worst queries: {worst_five}"
    )

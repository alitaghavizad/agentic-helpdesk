import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
import eval_retrieval  # noqa: E402


async def test_retrieval_recall_at_5_meets_gate():
    summary = await eval_retrieval.run_eval()
    assert summary["n"] == 60
    worst_five = sorted(summary["per_query"], key=lambda r: r["recall@5"])[:5]
    assert summary["recall@5"] >= 0.7, (
        f"Recall@5 = {summary['recall@5']:.4f} is below the 0.7 gate; worst queries: {worst_five}"
    )

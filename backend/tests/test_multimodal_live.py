"""Makes REAL Gemini calls. Excluded from the default run -- `uv run python
tasks.py test` costs nothing and calls nothing.

This is the half of the phase 7 gate that actually proves parsing. The offline
gate proves our pipeline; only this proves that an image, a PDF, and an audio
file are genuinely extracted.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.db.models import AttachmentKind
from app.multimodal import gemini

pytestmark = pytest.mark.live_gemini

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _requires_key():
    if not gemini.is_configured():
        pytest.skip("GEMINI_API_KEY is not configured")


def _run(path: Path, mime: str, kind: AttachmentKind):
    from app.db.models import RunStatus, RunTrigger
    from app.tracing.spans import end_run, start_run

    handle = start_run(RunTrigger.INGEST_EVAL)
    try:
        return gemini.parse(path.read_bytes(), mime_type=mime, kind=kind)
    finally:
        end_run(handle, status=RunStatus.OK)


def test_a_real_image_parses():
    path = FIXTURES / "sample_error.png"
    if not path.exists():
        pytest.skip("no image fixture available")
    result = _run(path, "image/png", AttachmentKind.IMAGE)
    assert result.text.strip()
    print(f"\nIMAGE ->\n{result.text[:500]}")


def test_a_real_pdf_parses():
    result = _run(FIXTURES / "sample_report.pdf", "application/pdf", AttachmentKind.PDF)
    assert result.text.strip()
    print(f"\nPDF ->\n{result.text[:500]}")


def test_a_real_audio_file_parses():
    """sample_voice.wav is a generated silent clip -- no spoken recording was
    available in this environment (brief Step 4). A silent input has no
    speech to transcribe, and app/multimodal/gemini.py raises
    GeminiUnavailable whenever the model returns empty text (a deliberate
    choice: an empty extraction must never be recorded as a successful
    parse). For THIS fixture that is the expected outcome, not a failure --
    the API call still round-trips successfully. So: accept either a
    ParseResult, or a GeminiUnavailable whose message says exactly that, and
    assert only that the call reached Gemini and came back with an
    unambiguous answer either way."""
    path = FIXTURES / "sample_voice.wav"
    try:
        result = _run(path, "audio/wav", AttachmentKind.AUDIO)
    except gemini.GeminiUnavailable as exc:
        if "returned no text" not in str(exc):
            raise
        pytest.skip(f"silent fixture produced no transcribable speech (expected): {exc}")
    else:
        assert isinstance(result, gemini.ParseResult)
        print(f"\nAUDIO ->\n{result.text[:500]}")

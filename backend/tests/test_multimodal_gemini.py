"""Every test here replaces the Gemini client. A real call costs money and
needs the network; the marked live tests in test_multimodal_live.py are the
only place a real one happens."""
from __future__ import annotations

import pytest

from app.db.models import AttachmentKind, RunStatus, RunTrigger, SpanKind
from app.multimodal import gemini


class FakeResponse:
    def __init__(self, text: str, prompt_tokens: int = 11, output_tokens: int = 7) -> None:
        self.text = text
        self.usage_metadata = type(
            "U", (), {"prompt_token_count": prompt_tokens, "candidates_token_count": output_tokens},
        )()


class FakeClient:
    def __init__(self, response=None, raises: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self._response = response or FakeResponse("extracted text")
        self._raises = raises
        self.models = self

    def generate_content(self, *, model, contents, **kwargs):
        self.calls.append({"model": model, "contents": contents})
        if self._raises:
            raise self._raises
        return self._response


@pytest.fixture()
def fake(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(gemini, "_client", client)
    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    return client


def test_every_kind_has_its_own_prompt():
    """Spec 11 specifies a task-specific extraction prompt per kind. A shared
    generic prompt would be a silent regression -- the audio prompt asks for a
    transcript, the PDF prompt asks for tables."""
    assert set(gemini.PROMPTS) == set(AttachmentKind)
    assert len({p for p in gemini.PROMPTS.values()}) == len(AttachmentKind)


def test_parse_returns_the_extracted_text_and_model(fake, cleanup_run):
    from app.tracing.spans import end_run, start_run

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        result = gemini.parse(b"\x89PNG fake", mime_type="image/png", kind=AttachmentKind.IMAGE)
        assert result.text == "extracted text"
        assert result.model
    finally:
        end_run(handle, status=RunStatus.OK)
        cleanup_run(handle.run_id)


def test_parse_uses_the_prompt_for_that_kind(fake, cleanup_run):
    from app.tracing.spans import end_run, start_run

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        gemini.parse(b"fake", mime_type="audio/wav", kind=AttachmentKind.AUDIO)
        sent = str(fake.calls[0]["contents"])
        assert "transcript" in sent.lower()
    finally:
        end_run(handle, status=RunStatus.OK)
        cleanup_run(handle.run_id)


def test_parse_records_a_parse_span_with_usage(db_session, cleanup_run, fake):
    """Spec 11: the call runs in a `parse` span recording model, token usage
    where reported, and latency."""
    from app.db.models import Span
    from app.tracing.spans import end_run, start_run

    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        gemini.parse(b"fake", mime_type="application/pdf", kind=AttachmentKind.PDF)
    finally:
        end_run(handle, status=RunStatus.OK)

    try:
        spans = db_session.query(Span).filter(
            Span.run_id == handle.run_id, Span.kind == SpanKind.PARSE,
        ).all()
        assert len(spans) == 1
        assert spans[0].input_tokens == 11
        assert spans[0].output_tokens == 7
        assert spans[0].model
    finally:
        cleanup_run(handle.run_id)


def test_parse_without_a_key_raises_gemini_unavailable(monkeypatch):
    monkeypatch.setattr(gemini, "is_configured", lambda: False)
    with pytest.raises(gemini.GeminiUnavailable):
        gemini.parse(b"fake", mime_type="image/png", kind=AttachmentKind.IMAGE)


def test_a_client_construction_failure_surfaces_as_gemini_unavailable(monkeypatch, cleanup_run):
    """`_get_client()` used to run outside `_traced_parse`'s try/except, so a
    construction failure (a malformed key, a broken transport dependency)
    would escape as whatever raw exception the client raised instead of
    GeminiUnavailable -- contradicting that class's docstring promise that
    a parse failure never reaches the uploader as a 500."""
    from app.tracing.spans import end_run, start_run

    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    monkeypatch.setattr(gemini, "_client", None)

    def _boom():
        raise RuntimeError("could not construct genai.Client")

    monkeypatch.setattr(gemini, "_get_client", _boom)
    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        with pytest.raises(gemini.GeminiUnavailable):
            gemini.parse(b"fake", mime_type="image/png", kind=AttachmentKind.IMAGE)
    finally:
        end_run(handle, status=RunStatus.ERROR)
        cleanup_run(handle.run_id)


class FakeNotFoundError(Exception):
    """Shaped like google.genai.errors.ClientError for a retired model: a
    404 status code and a NOT_FOUND status string as real attributes, which
    is how the real SDK exposes it (verified directly against the live API
    for a retired `gemini-2.5-flash`)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = 404
        self.status = "NOT_FOUND"


def test_a_404_shaped_failure_names_the_configured_model(monkeypatch, cleanup_run):
    """`_check_model_once` only checks list membership, which does not
    guarantee a model is callable -- Gemini has been observed serving
    generateContent in a retired model's supported_actions while a real
    call 404s. When that happens, the diagnostic must name the configured
    GEMINI_MODEL so it is obvious which setting to fix, not just echo the
    SDK's generic error text."""
    from app.config import get_settings
    from app.tracing.spans import end_run, start_run

    configured_model = get_settings().gemini_model
    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    monkeypatch.setattr(
        gemini, "_client",
        FakeClient(raises=FakeNotFoundError(
            f"404 NOT_FOUND. This model models/{configured_model} is no longer available",
        )),
    )
    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        with pytest.raises(gemini.GeminiUnavailable) as excinfo:
            gemini.parse(b"fake", mime_type="image/png", kind=AttachmentKind.IMAGE)
        message = str(excinfo.value)
        assert configured_model in message
        assert "GEMINI_MODEL" in message
    finally:
        end_run(handle, status=RunStatus.ERROR)
        cleanup_run(handle.run_id)


def test_an_empty_response_is_an_error_not_silent_success(monkeypatch, cleanup_run):
    """A model that returns nothing has not parsed the file. Returning ''
    would store an empty parsed_text as though it succeeded."""
    from app.tracing.spans import end_run, start_run

    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    monkeypatch.setattr(gemini, "_client", FakeClient(response=FakeResponse("   ")))
    handle = start_run(RunTrigger.CHAT_TURN)
    try:
        with pytest.raises(gemini.GeminiUnavailable):
            gemini.parse(b"fake", mime_type="image/png", kind=AttachmentKind.IMAGE)
    finally:
        end_run(handle, status=RunStatus.ERROR)
        cleanup_run(handle.run_id)

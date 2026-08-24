import pytest

from app.rag.backend import get_rag_backend
from app.rag.direct_client import DirectChromaBackend
from app.rag.mcp_client import McpChromaBackend


def test_get_rag_backend_direct():
    backend = get_rag_backend("direct")
    assert isinstance(backend, DirectChromaBackend)


def test_get_rag_backend_mcp():
    backend = get_rag_backend("mcp")
    assert isinstance(backend, McpChromaBackend)


def test_get_rag_backend_unknown_raises():
    with pytest.raises(ValueError, match="Unknown CHROMA_BACKEND"):
        get_rag_backend("nonsense")


def test_get_rag_backend_defaults_to_configured_setting():
    from app.config import get_settings

    settings = get_settings()
    backend = get_rag_backend()
    expected_type = DirectChromaBackend if settings.chroma_backend == "direct" else McpChromaBackend
    assert isinstance(backend, expected_type)

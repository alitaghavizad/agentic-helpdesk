from __future__ import annotations

import io

import pytest

from app.db.models import EscalationAuthority, Role, User
from app.multimodal import gemini, service

PNG = b"\x89PNG\r\n\x1a\n" + b"deadbeef" * 4


def _login(client, db_session, *, username: str, role: Role, helpdesk_ref: str | None = None) -> tuple:
    """Copied from tests/test_tickets_router.py -- there is no shared
    auth_headers_for_role fixture in this project, so every router test
    module logs a real user in through the API itself."""
    from app.auth.security import hash_password

    user = User(
        username=username, email=f"{username}@northstar.example", full_name=username.title(),
        password_hash=hash_password("Passw0rd!dev"), role=role, helpdesk_ref=helpdesk_ref,
        specialization="Network and VPN Support" if helpdesk_ref else None,
        escalation_authority=EscalationAuthority.STANDARD if helpdesk_ref else None,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(service, "storage_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def parses_ok(monkeypatch):
    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    monkeypatch.setattr(
        gemini, "parse",
        lambda data, *, mime_type, kind: gemini.ParseResult(text="Disk full.", model="gemini-test"),
    )


def _upload(client, headers, conversation_id, *, name="shot.png", mime="image/png", data=PNG):
    return client.post(
        f"/api/conversations/{conversation_id}/attachments",
        files={"file": (name, io.BytesIO(data), mime)},
        headers=headers,
    )


def test_upload_stores_parses_and_returns_the_attachment(client, db_session, storage, parses_ok):
    user, headers = _login(client, db_session, username="mmup", role=Role.EMPLOYEE)
    conv = client.post("/api/conversations", json={"title": "t"}, headers=headers).json()

    response = _upload(client, headers, conv["id"])
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["parse_status"] == "parsed"
    assert body["kind"] == "image"
    assert body["filename"] == "shot.png"


def test_a_mismatched_file_is_rejected_with_its_reason(client, db_session, storage, parses_ok):
    user, headers = _login(client, db_session, username="mmbad", role=Role.EMPLOYEE)
    conv = client.post("/api/conversations", json={"title": "t"}, headers=headers).json()

    response = _upload(client, headers, conv["id"], data=b"%PDF-1.4 pretending")
    assert response.status_code == 400
    assert "match" in response.json()["detail"].lower()


def test_uploading_to_someone_elses_conversation_is_404(client, db_session, storage, parses_ok):
    _owner, owner_headers = _login(client, db_session, username="mmowner", role=Role.EMPLOYEE)
    conv = client.post("/api/conversations", json={"title": "t"}, headers=owner_headers).json()
    _other, other_headers = _login(client, db_session, username="mmnosy", role=Role.EMPLOYEE)

    assert _upload(client, other_headers, conv["id"]).status_code == 404


def test_retrieval_returns_the_bytes_to_the_owner(client, db_session, storage, parses_ok):
    _user, headers = _login(client, db_session, username="mmget", role=Role.EMPLOYEE)
    conv = client.post("/api/conversations", json={"title": "t"}, headers=headers).json()
    attachment_id = _upload(client, headers, conv["id"]).json()["id"]

    response = client.get(f"/api/attachments/{attachment_id}", headers=headers)
    assert response.status_code == 200
    assert response.content == PNG


def test_retrieval_by_an_unrelated_user_is_404_not_403(client, db_session, storage, parses_ok):
    """404, so the endpoint never confirms that an id exists."""
    _owner, owner_headers = _login(client, db_session, username="mmown2", role=Role.EMPLOYEE)
    conv = client.post("/api/conversations", json={"title": "t"}, headers=owner_headers).json()
    attachment_id = _upload(client, owner_headers, conv["id"]).json()["id"]

    _other, other_headers = _login(client, db_session, username="mmnosy2", role=Role.EMPLOYEE)
    assert client.get(f"/api/attachments/{attachment_id}", headers=other_headers).status_code == 404


def test_an_admin_may_retrieve_any_attachment(client, db_session, storage, parses_ok):
    _owner, owner_headers = _login(client, db_session, username="mmown3", role=Role.EMPLOYEE)
    conv = client.post("/api/conversations", json={"title": "t"}, headers=owner_headers).json()
    attachment_id = _upload(client, owner_headers, conv["id"]).json()["id"]

    _admin, admin_headers = _login(client, db_session, username="mmadmin", role=Role.ADMIN)
    assert client.get(f"/api/attachments/{attachment_id}", headers=admin_headers).status_code == 200


def test_upload_is_503_when_gemini_is_not_configured(client, db_session, storage, monkeypatch):
    monkeypatch.setattr(gemini, "is_configured", lambda: False)
    _user, headers = _login(client, db_session, username="mmnokey", role=Role.EMPLOYEE)
    conv = client.post("/api/conversations", json={"title": "t"}, headers=headers).json()

    response = _upload(client, headers, conv["id"])
    assert response.status_code == 503
    assert "attachment" in response.json()["detail"].lower()


def test_request_attachment_is_absent_from_the_catalog_without_a_key(monkeypatch):
    """Spec 11: the agent must not be able to ask for something the system
    cannot accept."""
    from app.agent import registry

    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    assert any(_tool_name(t) == "request_attachment" for t in registry.to_anthropic_tool_params())

    monkeypatch.setattr(gemini, "is_configured", lambda: False)
    assert not any(_tool_name(t) == "request_attachment" for t in registry.to_anthropic_tool_params())


def _tool_name(tool) -> str:
    return tool["name"] if isinstance(tool, dict) else tool.get("name", "")

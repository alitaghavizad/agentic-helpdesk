from __future__ import annotations

import uuid

from app.auth.security import hash_password
from app.chat.service import append_message
from app.db.models import Conversation, MessageRole, Role, Run, RunStatus, RunTrigger, User


def _login(client, db_session, *, username: str, role: Role):
    user = User(
        username=username, email=f"{username}@northstar.example",
        full_name=username.title(), password_hash=hash_password("Passw0rd!dev"), role=role,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_detail_returns_transcript_and_runs(client, db_session):
    _, headers = _login(client, db_session, username="detail_admin", role=Role.ADMIN)
    conv = Conversation(guest_name="Guest", guest_email="g@example.com", title="Printer down")
    db_session.add(conv)
    db_session.commit()
    append_message(db_session, conv.id, MessageRole.USER, [{"type": "text", "text": "printer"}])
    # Two runs, so the ordering assertion below has something to order.
    db_session.add(Run(conversation_id=conv.id, trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK))
    db_session.add(Run(conversation_id=conv.id, trigger=RunTrigger.CHAT_TURN, status=RunStatus.ERROR))
    db_session.commit()

    body = client.get(f"/api/admin/conversations/{conv.id}", headers=headers).json()

    assert body["conversation"]["title"] == "Printer down"
    assert body["conversation"]["guest_email"] == "g@example.com"
    assert [m["content"] for m in body["messages"]] == [[{"type": "text", "text": "printer"}]]
    assert len(body["runs"]) == 2


def test_detail_excludes_runs_from_other_conversations(client, db_session):
    """The screen links each run into the trace view. A run from another
    conversation appearing here would send an admin to the wrong tree."""
    _, headers = _login(client, db_session, username="detail_scope", role=Role.ADMIN)
    mine = Conversation(guest_name="G", guest_email="g@example.com", title="mine")
    other = Conversation(guest_name="G", guest_email="g@example.com", title="other")
    db_session.add_all([mine, other])
    db_session.commit()
    db_session.add(Run(conversation_id=other.id, trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK))
    db_session.commit()

    body = client.get(f"/api/admin/conversations/{mine.id}", headers=headers).json()

    assert body["runs"] == []


def test_detail_404s_for_an_unknown_conversation(client, db_session):
    _, headers = _login(client, db_session, username="detail_404", role=Role.ADMIN)

    resp = client.get(f"/api/admin/conversations/{uuid.uuid4()}", headers=headers)

    assert resp.status_code == 404


def test_detail_is_admin_only(client, db_session):
    _, headers = _login(client, db_session, username="detail_employee", role=Role.EMPLOYEE)
    conv = Conversation(guest_name="G", guest_email="g@example.com", title="t")
    db_session.add(conv)
    db_session.commit()

    resp = client.get(f"/api/admin/conversations/{conv.id}", headers=headers)

    assert resp.status_code == 403

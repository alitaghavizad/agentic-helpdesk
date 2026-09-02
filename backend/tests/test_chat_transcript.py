from __future__ import annotations

from app.auth.security import hash_password
from app.db.models import Conversation, MessageRole, Role, User
from app.chat.service import append_message


def _login(client, db_session, *, username: str, role: Role = Role.EMPLOYEE):
    user = User(
        username=username, email=f"{username}@northstar.example",
        full_name=username.title(), password_hash=hash_password("Passw0rd!dev"), role=role,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_get_conversation_returns_its_transcript(client, db_session):
    user, headers = _login(client, db_session, username="transcript_owner")
    conv = Conversation(user_id=user.id, title="VPN help")
    db_session.add(conv)
    db_session.commit()
    append_message(db_session, conv.id, MessageRole.USER, [{"type": "text", "text": "hello"}])
    append_message(db_session, conv.id, MessageRole.ASSISTANT, [{"type": "text", "text": "hi"}])

    body = client.get(f"/api/conversations/{conv.id}", headers=headers).json()

    assert [m["role"] for m in body["messages"]] == ["user", "assistant"]
    assert body["messages"][0]["content"] == [{"type": "text", "text": "hello"}]
    assert body["messages"][0]["created_at"] is not None


def test_transcript_excludes_system_messages(client, db_session):
    user, headers = _login(client, db_session, username="transcript_system")
    conv = Conversation(user_id=user.id, title="t")
    db_session.add(conv)
    db_session.commit()
    append_message(db_session, conv.id, MessageRole.SYSTEM, [{"type": "text", "text": "secret prompt"}])
    append_message(db_session, conv.id, MessageRole.USER, [{"type": "text", "text": "visible"}])

    body = client.get(f"/api/conversations/{conv.id}", headers=headers).json()

    assert [m["role"] for m in body["messages"]] == ["user"]


def test_transcript_is_not_readable_by_another_employee(client, db_session):
    owner, _ = _login(client, db_session, username="transcript_a")
    conv = Conversation(user_id=owner.id, title="private")
    db_session.add(conv)
    db_session.commit()
    append_message(db_session, conv.id, MessageRole.USER, [{"type": "text", "text": "private text"}])
    _, other_headers = _login(client, db_session, username="transcript_b")

    resp = client.get(f"/api/conversations/{conv.id}", headers=other_headers)

    assert resp.status_code == 404
    assert "private text" not in resp.text


def test_list_conversations_does_not_carry_transcripts(client, db_session):
    """The list endpoint shares ConversationResponse. Loading every
    transcript to render a sidebar would read the whole message table."""
    user, headers = _login(client, db_session, username="transcript_list")
    conv = Conversation(user_id=user.id, title="t")
    db_session.add(conv)
    db_session.commit()
    append_message(db_session, conv.id, MessageRole.USER, [{"type": "text", "text": "body text"}])

    body = client.get("/api/conversations", headers=headers).json()

    assert body[0]["messages"] == []

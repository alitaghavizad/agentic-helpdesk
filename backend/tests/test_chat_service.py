from __future__ import annotations

import uuid

from app.chat.service import append_message, create_conversation, derive_conversation_title, get_conversation, list_conversations, load_history
from app.db.models import MessageRole, Role, User
from app.rbac.policy import Principal


def _make_user(db_session):
    user = User(username="jdoe", email="jdoe@northstar.example", full_name="Jane Doe", password_hash="x", role=Role.EMPLOYEE)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _principal_for(user) -> Principal:
    return Principal(kind="user", user_id=str(user.id), role=user.role.value, clearance=None, department=None, employee_ref=None, helpdesk_ref=None)


def _guest_principal() -> Principal:
    return Principal(kind="guest", user_id=None, role="guest", clearance=None, department=None, employee_ref=None, helpdesk_ref=None)


def test_create_conversation_for_authenticated_user(db_session):
    user = _make_user(db_session)
    conv = create_conversation(db_session, _principal_for(user))
    assert conv.user_id == user.id
    assert conv.guest_email is None


def test_create_conversation_for_guest_requires_guest_contact_info(db_session):
    conv = create_conversation(db_session, _guest_principal(), guest_name="Visitor", guest_email="visitor@example.com")
    assert conv.user_id is None
    assert conv.guest_email == "visitor@example.com"


def test_derive_conversation_title_returns_short_text_unchanged():
    assert derive_conversation_title("my mouse is laggy") == "my mouse is laggy"


def test_derive_conversation_title_strips_surrounding_whitespace():
    assert derive_conversation_title("  hi there  \n") == "hi there"


def test_derive_conversation_title_truncates_long_text_at_a_word_boundary():
    text = "My VPN client has started rejecting the certificate the helpdesk issued after last week's root CA rotation"
    title = derive_conversation_title(text)
    assert len(title) <= 63  # 60 + "..."
    assert title.endswith("...")
    assert not title[:-3].endswith(" ")  # trimmed the trailing partial word, not just cut mid-word
    assert text.startswith(title[:-3])


def test_derive_conversation_title_of_blank_text_is_empty():
    """Callers must be able to tell "nothing to derive from" apart from a
    real title, so they can leave an existing None title alone rather than
    overwriting it with an empty string."""
    assert derive_conversation_title("   ") == ""


def test_list_conversations_is_row_scoped_to_the_principal(db_session):
    user_a = _make_user(db_session)
    user_b = User(username="other", email="other@northstar.example", full_name="Other User", password_hash="x", role=Role.EMPLOYEE)
    db_session.add(user_b)
    db_session.commit()
    db_session.refresh(user_b)

    create_conversation(db_session, _principal_for(user_a))
    create_conversation(db_session, _principal_for(user_b))

    result = list_conversations(db_session, _principal_for(user_a))
    assert len(result) == 1
    assert result[0].user_id == user_a.id


def test_get_conversation_denies_access_to_a_conversation_owned_by_someone_else(db_session):
    user_a = _make_user(db_session)
    user_b = User(username="other2", email="other2@northstar.example", full_name="Other User Two", password_hash="x", role=Role.EMPLOYEE)
    db_session.add(user_b)
    db_session.commit()
    db_session.refresh(user_b)

    conv = create_conversation(db_session, _principal_for(user_a))
    assert get_conversation(db_session, _principal_for(user_b), conv.id) is None
    assert get_conversation(db_session, _principal_for(user_a), conv.id) is not None


def test_append_message_and_load_history_round_trip(db_session):
    user = _make_user(db_session)
    conv = create_conversation(db_session, _principal_for(user))

    append_message(db_session, conv.id, MessageRole.USER, [{"type": "text", "text": "Hello"}])
    append_message(db_session, conv.id, MessageRole.ASSISTANT, [{"type": "text", "text": "Hi there"}])

    history = load_history(db_session, conv.id)
    assert history == [
        {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Hi there"}]},
    ]

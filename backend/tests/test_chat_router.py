from __future__ import annotations

import json
import uuid

import pytest

from app.db.models import Conversation, Role, User
from tests.support.fake_anthropic import FakeAnthropicClient, make_text_message

# Populated by test_sse_message_endpoint_streams_events with the ids of the
# User/Conversation rows it hard-commits, so _cleanup_sse_test_orphans_after_module
# (below) can sweep them once every test in this module has finished.
_sse_orphan_ids: dict[str, list] = {"user_ids": [], "conversation_ids": []}


@pytest.fixture(scope="module", autouse=True)
def _cleanup_sse_test_orphans_after_module():
    """See test_sse_message_endpoint_streams_events's docstring: that test
    must create its User/Conversation through real, hard-committing
    sessions (so run_turn()'s own, independently-committing tracing
    session can see them), and must NOT delete them itself -- doing so
    mid-test would deadlock against the FOR KEY SHARE lock db_session's
    still-open transaction holds via the Run-referencing ASSISTANT Message
    it inserts.

    Once every test in this module has finished, every db_session-bound
    transaction has already rolled back and released its locks (and, not
    incidentally, that ASSISTANT Message itself: it was only ever inserted
    via db_session, so it never survives past that rollback -- only the
    hard-committed User/Conversation/Run/Span rows persist), so a final
    sweep here can safely delete this module's orphaned User/Conversation
    rows and anything hanging off them (Run, Span). This mirrors
    test_agent_loop.py's `_sweep_fixed_employee_row_after_module` exactly
    -- including its UsageCounter cleanup: run_turn() -> check_and_record_
    usage() commits a permanent UsageCounter(user_key, hour-bucket) row
    through its own independently-committing session, keyed to
    `principal.user_id` (see send_message_endpoint's `user_key =
    principal.user_id if principal.kind == "user" else ...` in
    app/chat/router.py) -- i.e. str(user.id) for this test's hard-
    committed user. Without this, each run of this suite leaves one more
    User row *and* one more UsageCounter row behind in the shared test
    Postgres instance; the User leak is exactly what broke
    tests/test_seed.py's exact-count assertions (`assert total == 126`)
    after a few manual re-runs of this file while developing it.
    """
    yield
    from app.db.models import Run, Span, UsageCounter
    from app.db.session import get_sessionmaker

    Session = get_sessionmaker()
    with Session() as session:
        conv_ids = list(_sse_orphan_ids["conversation_ids"])
        user_ids = list(_sse_orphan_ids["user_ids"])
        if user_ids:
            session.query(UsageCounter).filter(UsageCounter.user_key.in_([str(uid) for uid in user_ids])).delete(synchronize_session=False)
        if conv_ids:
            run_ids = [row[0] for row in session.query(Run.id).filter(Run.conversation_id.in_(conv_ids))]
            if run_ids:
                session.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
                session.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
            session.query(Conversation).filter(Conversation.id.in_(conv_ids)).delete(synchronize_session=False)
        if user_ids:
            session.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        session.commit()


def _make_user_and_login(client, db_session):
    from app.auth.security import hash_password
    user = User(username="chatuser", email="chatuser@northstar.example", full_name="Chat User", password_hash=hash_password("Passw0rd!dev"), role=Role.EMPLOYEE)
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": "chatuser", "password": "Passw0rd!dev"})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _make_hard_committed_user_and_login(client) -> tuple[dict, User]:
    """Only for test_sse_message_endpoint_streams_events below -- see that
    test's docstring for the full mechanism (the same cross-connection
    FK-visibility gap Task 11's review diagnosed for test_agent_loop.py's
    _conversation() helper).

    The other two tests in this file can use the ordinary
    _make_user_and_login above (User inserted via db_session, cleaned up
    automatically by db_session's rollback) because they never call
    run_turn()/tracing and everything they touch stays on db_session's one
    connection. This test is different: the Conversation it exercises must
    be a real, hard commit so that run_turn()'s own, independently-
    committing tracing session can see it. Since Conversation.user_id
    FK-references users.id, the User backing the login must ALSO be a
    real, hard commit -- a db_session-inserted User is only ever a
    SAVEPOINT release (db_session's outer transaction is rolled back at
    teardown, never truly committed), which would be just as invisible to
    a hard-committing connection's FK check as an ordinary
    POST /api/conversations conversation was in the original bug.

    We insert the User directly through get_sessionmaker() (a real,
    separate connection), then log in normally through the client. This is
    safe: Postgres's READ COMMITTED isolation gives db_session's login
    SELECT a fresh per-statement snapshot that sees this already-committed
    row regardless of when db_session's own outer transaction began. The
    username/email carry a random suffix so repeated runs of this suite
    don't collide on the unique constraints -- this row, like the
    Conversation/Run/Span rows the test itself creates, is swept up by
    _cleanup_sse_test_orphans_after_module (top of this file) once every
    test in this module has finished, rather than cleaned up inline (see
    that test's docstring for why it can't be inline).

    The id is recorded into _sse_orphan_ids immediately after this
    function's own commit succeeds -- not by the caller after both this
    and _hard_committed_conversation return -- so that a failure partway
    through (e.g. the login POST below) can never leak a hard-committed
    User row the module-teardown sweep doesn't know about.
    """
    from app.auth.security import hash_password
    from app.db.session import get_sessionmaker

    suffix = uuid.uuid4().hex[:8]
    Session = get_sessionmaker()
    with Session() as session:
        user = User(
            username=f"chatuser-sse-{suffix}", email=f"chatuser-sse-{suffix}@northstar.example",
            full_name="Chat User SSE", password_hash=hash_password("Passw0rd!dev"), role=Role.EMPLOYEE,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
    _sse_orphan_ids["user_ids"].append(user.id)

    resp = client.post("/api/auth/login", json={"username": user.username, "password": "Passw0rd!dev"})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}, user


def _hard_committed_conversation(user_id: uuid.UUID) -> Conversation:
    """Same mechanism as _make_hard_committed_user_and_login above and as
    test_agent_loop.py's _conversation(): inserts the Conversation through
    a real, hard-committing session instead of the brief's original
    POST /api/conversations call, which routes through the shared
    db_session and only ever SAVEPOINT-commits it -- invisible to
    tracing's own connection the moment run_turn() calls start_run().
    `user_id` must already be visible on this connection (i.e. a real,
    hard-committed User, as returned by _make_hard_committed_user_and_login
    above), or this insert's own FK check on conversations_user_id_fkey
    would fail for the identical reason.

    Like _make_hard_committed_user_and_login, this records the new
    Conversation's id into _sse_orphan_ids itself, immediately after its
    own commit, so a partial failure never leaves an untracked orphan.
    """
    from app.db.session import get_sessionmaker

    Session = get_sessionmaker()
    with Session() as session:
        conv = Conversation(user_id=user_id)
        session.add(conv)
        session.commit()
        session.refresh(conv)
    _sse_orphan_ids["conversation_ids"].append(conv.id)
    return conv


def test_create_and_list_conversations(client, db_session):
    headers = _make_user_and_login(client, db_session)
    resp = client.post("/api/conversations", json={"title": "Test chat"}, headers=headers)
    assert resp.status_code == 200
    conv_id = resp.json()["id"]

    resp = client.get("/api/conversations", headers=headers)
    assert resp.status_code == 200
    assert any(c["id"] == conv_id for c in resp.json())


def test_get_conversation_returns_404_for_nonexistent_conversation(client, db_session):
    headers = _make_user_and_login(client, db_session)
    resp = client.get(f"/api/conversations/{uuid.uuid4()}", headers=headers)
    assert resp.status_code == 404


def _guest_login(client) -> dict:
    resp = client.post("/api/auth/guest", json={"name": "Visitor", "email": "visitor@example.com"})
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_guest_can_create_and_read_back_a_conversation(client):
    headers = _guest_login(client)
    resp = client.post("/api/conversations", json={"title": "Guest chat"}, headers=headers)
    assert resp.status_code == 200
    conv_id = resp.json()["id"]

    resp = client.get(f"/api/conversations/{conv_id}", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["id"] == conv_id


def test_sse_message_endpoint_streams_events(client, db_session, monkeypatch):
    """The Conversation (and its backing User) this test exercises are
    created through real, hard-committing sessions (see
    _make_hard_committed_user_and_login and _hard_committed_conversation
    above), not via a POST /api/conversations call routed through the
    shared db_session. This is the exact same cross-connection
    FK-visibility mechanism Task 11's review diagnosed for
    test_agent_loop.py: run_turn() calls app.tracing.start_run(), which
    inserts a Run row via its own, independently-committing session (a
    different physical Postgres connection). Under READ COMMITTED, that
    connection cannot see a Conversation row that only exists as a
    SAVEPOINT inside db_session's still-open outer transaction -- a
    Conversation created via the HTTP call above would reproduce
    `IntegrityError: ... violates foreign key constraint
    "runs_conversation_id_fkey"` the moment run_turn() calls start_run().

    The endpoint's own append_message() call then inserts an ASSISTANT
    Message (via the shared db_session) that FK-references this same
    real, hard-committed Run row. Exactly as in test_agent_loop.py's
    test_run_turn_emits_task_recorded_and_ticket_created_events, we must
    NOT attempt to clean up the User/Conversation/Run/Span rows created
    for this test *here*: doing so would deadlock against the FOR KEY
    SHARE lock db_session's still-open transaction holds via that
    Message row's FK. Instead we record their ids in _sse_orphan_ids for
    _cleanup_sse_test_orphans_after_module (top of this file) to sweep
    once this module's tests -- and their db_session transactions -- have
    all finished.
    """
    headers, user = _make_hard_committed_user_and_login(client)
    conv = _hard_committed_conversation(user.id)
    conv_id = str(conv.id)

    fake_client = FakeAnthropicClient([make_text_message(text="Hello! How can I help?")])
    import app.chat.router as router_module
    monkeypatch.setattr(router_module, "_anthropic_client", fake_client)

    with client.stream("POST", f"/api/conversations/{conv_id}/messages", json={"content": "Hi there"}, headers=headers) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        body = "".join(resp.iter_text())

    events = [json.loads(line.removeprefix("data: ")) for line in body.splitlines() if line.startswith("data: ")]
    assert any(e["type"] == "token" for e in events)
    assert events[-1]["type"] == "done"


def test_the_first_message_sets_the_conversation_title_and_later_ones_dont(client, db_session, monkeypatch):
    """No code path anywhere in this app has ever set a conversation's
    title -- POST /api/conversations is always called with no title
    argument, so every conversation showed as "(untitled)" forever. Fixed
    by deriving one from the first user message; this pins both halves:
    the first message sets it, and a second message never overwrites it,
    the same way a real subject line isn't replaced by a reply."""
    headers, user = _make_hard_committed_user_and_login(client)
    conv = _hard_committed_conversation(user.id)
    conv_id = str(conv.id)

    fake_client = FakeAnthropicClient([
        make_text_message(text="Happy to help."),
        make_text_message(text="Got it, thanks for the detail."),
    ])
    import app.chat.router as router_module
    monkeypatch.setattr(router_module, "_anthropic_client", fake_client)

    with client.stream(
        "POST", f"/api/conversations/{conv_id}/messages",
        json={"content": "My VPN client rejects the certificate after the root CA rotation"}, headers=headers,
    ) as resp:
        assert resp.status_code == 200
        "".join(resp.iter_text())

    db_session.expire_all()
    stored = db_session.query(Conversation).filter_by(id=conv.id).one()
    assert stored.title == "My VPN client rejects the certificate after the root CA..."

    with client.stream(
        "POST", f"/api/conversations/{conv_id}/messages",
        json={"content": "It started right after IT rotated the root certificate"}, headers=headers,
    ) as resp:
        assert resp.status_code == 200
        "".join(resp.iter_text())

    db_session.expire_all()
    stored = db_session.query(Conversation).filter_by(id=conv.id).one()
    assert stored.title == "My VPN client rejects the certificate after the root CA..."

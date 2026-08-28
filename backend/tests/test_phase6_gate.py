"""Spec 18, phase 6 gate: 'Approve -> execute -> SSE + email.'

This is one continuous path, deliberately not decomposed: each half passing
in isolation is what the per-module tests already prove (test_approvals_service.py
and test_admin_approvals_router.py prove approve-then-execute in isolation;
test_notifications_router.py proves the SSE stream in isolation). What this
test adds is that a SINGLE admin HTTP approval causes, in one flow: the
action to execute, a real `outbound_emails` row to reach `sent`, AND a live
SSE subscriber to receive the decision -- in the right order, over one
running app.

Two fixture problems this file solves that the per-module tests already
solved individually, combined here because the gate needs both at once:

1. `auth_headers_for_role` / `current_user_for_role` do not exist anywhere
   in this codebase -- there is no such fixture. Auth here is a REAL login
   through `/api/auth/login`, the same helper pattern used by
   tests/test_tickets_router.py, tests/test_notifications_router.py, and
   tests/test_admin_approvals_router.py.

2. httpx 0.28's stock `httpx.ASGITransport` cannot stream this test's SSE
   response: it does `await self.app(scope, receive, send)` and only builds
   the `Response` after that await returns, but the notification stream's
   generator never finishes on its own (it loops forever emitting
   keepalives) -- so `ac.stream(...)` against the stock transport hangs
   forever. tests/test_notifications_router.py already solved this with a
   custom `_LiveASGITransport` that runs the ASGI app as a background task
   and streams response body chunks out through a queue as they arrive.
   That class (and its `_QueueByteStream` helper) is duplicated here rather
   than imported or factored into conftest.py -- see the docstring on
   `_LiveASGITransport` below for why.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import uuid as _uuid

import httpx
import pytest

from app.db.models import (
    ApprovalActionType, ApprovalRequest, ApprovalStatus, Conversation, EmailStatus,
    OutboundEmail, RiskLevel, Role, Run, Span, User,
)
from app.db.session import get_sessionmaker
from app.notifications import email as email_module


def _login(client, db_session, *, username: str, role: Role) -> tuple[User, dict]:
    """Copied verbatim (module-local, not shared) from
    tests/test_tickets_router.py / tests/test_admin_approvals_router.py's
    `_login`: a real login through the API rather than a fabricated token,
    so this test exercises the same auth path production traffic does.
    Used here only for the ADMIN principal -- the requester needs a
    different construction path, see `_hard_commit_requester_and_conversation`
    below."""
    from app.auth.security import hash_password

    user = User(
        username=username, email=f"{username}@northstar.example", full_name=username.title(),
        password_hash=hash_password("Passw0rd!dev"), role=role,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _login_existing(client, *, username: str) -> dict:
    """Logs in a User row that was already committed elsewhere (by
    `_hard_commit_requester_and_conversation`, on its own connection) rather
    than creating one -- the requester in this test must be visible to a
    SECOND, independently-committing connection (tracing's) before decide()
    ever runs, which rules out creating it through `db_session` the way
    `_login` above does. Logging in reads through `db_session` regardless
    (the `client` fixture's dependency override), which is fine: under
    READ COMMITTED, a row hard-committed on another connection is visible
    to db_session's next statement even though db_session's own outer
    transaction started earlier."""
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


class _AcceptingTransport:
    """Stands in for app.notifications.email._transport (normally an
    SmtplibTransport that would open a real socket to Gmail using the
    credentials in .env -- see the ABSOLUTE REQUIREMENT in this phase's
    task brief). Records every recipient it is asked to send to instead of
    touching a network. Installed via monkeypatch before the approval is
    ever decided, so nothing can reach the real transport."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    def send(self, message, *, to_address: str) -> str:
        self.sent.append(to_address)
        return "250 2.0.0 OK"


# decide()'s approval path calls tracing.start_run(), which inserts a Run row
# -- referencing conversation_id and requester_user_id -- through its OWN,
# independently-committing connection (see app/tracing/spans.py's module
# docstring). A Conversation/User created only through db_session is never
# more than a SAVEPOINT release under this file's db_session fixture, so it
# is invisible to that separate connection's FK check under READ COMMITTED:
# decide() would fail immediately with `IntegrityError ...
# runs_conversation_id_fkey` (or ...runs_user_id_fkey) the instant it calls
# start_run(). This is the identical, already-documented gap
# tests/test_approvals_service.py's `pending_request` fixture and
# tests/test_admin_approvals_router.py's `pending` fixture both solve for
# their own tests. `_hard_commit_requester_and_conversation` below applies
# the same fix: it creates the requester User and Conversation through a
# REAL, hard-committing session, so both rows are visible to every
# connection -- including tracing's -- before decide() ever runs. Recorded
# here so the module-scoped sweep fixture can delete them once every test
# has released its db_session lock on them.
_hard_committed_rows: list[tuple[_uuid.UUID, _uuid.UUID]] = []  # (conversation_id, requester_user_id)


@pytest.fixture(scope="module", autouse=True)
def _sweep_hard_committed_rows_after_module():
    """Deletes the Conversation/User/Run/Span rows hard-committed below, but
    only after every test in this module has finished. This file's
    db_session holds a FOR KEY SHARE lock on its Conversation/User row
    (taken when the ApprovalRequest was inserted referencing them) for as
    long as the test's transaction stays open, so deleting them any earlier
    would hang waiting on a lock that only releases at that test's own
    teardown. Mirrors test_approvals_service.py's and
    test_admin_approvals_router.py's identically-motivated module-scoped
    final sweeps."""
    yield
    Session = get_sessionmaker()
    with Session() as session:
        conversation_ids = [c for c, _u in _hard_committed_rows]
        user_ids = [u for _c, u in _hard_committed_rows]
        if conversation_ids:
            run_ids = [
                r for (r,) in session.query(Run.id).filter(Run.conversation_id.in_(conversation_ids))
            ]
            if run_ids:
                session.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
                session.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
            session.query(Conversation).filter(
                Conversation.id.in_(conversation_ids)
            ).delete(synchronize_session=False)
        if user_ids:
            session.query(User).filter(User.id.in_(user_ids)).delete(synchronize_session=False)
        session.commit()


def _hard_commit_requester_and_conversation(*, username: str) -> tuple[_uuid.UUID, _uuid.UUID]:
    """Returns (requester_user_id, conversation_id), both committed on a
    throwaway connection of their own so tracing's separate connection can
    see them before decide() runs. See the module comment above for why
    this is necessary."""
    from app.auth.security import hash_password

    Session = get_sessionmaker()
    with Session() as hard_session:
        requester = User(
            username=username, email=f"{username}@northstar.example", full_name=username.title(),
            password_hash=hash_password("Passw0rd!dev"), role=Role.EMPLOYEE,
        )
        hard_session.add(requester)
        hard_session.flush()
        conv = Conversation(user_id=requester.id, title="VPN help")
        hard_session.add(conv)
        hard_session.commit()
        requester_id, conversation_id = requester.id, conv.id
    _hard_committed_rows.append((conversation_id, requester_id))
    return requester_id, conversation_id


class _QueueByteStream(httpx.AsyncByteStream):
    """Backs the httpx.Response returned by _LiveASGITransport: an async
    byte-iterable pulled off a queue that the running ASGI app keeps
    feeding, rather than a fixed, pre-collected buffer. Copied verbatim
    from tests/test_notifications_router.py."""

    def __init__(self, queue: "asyncio.Queue[bytes | None]") -> None:
        self._queue = queue

    async def __aiter__(self):
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            yield chunk


class _LiveASGITransport(httpx.AsyncBaseTransport):
    """Duplicated verbatim from tests/test_notifications_router.py rather
    than imported from it or factored into conftest.py.

    Factoring it into conftest.py would be reasonable and arguably better;
    it is duplicated here instead to keep this file's diff self-contained
    to the phase 6 gate and to avoid touching a shared fixture file
    (conftest.py) as a side effect of an unrelated task -- and because a
    change there risks the notifications router's own tests, which this
    task's brief explicitly calls out as a thing to protect
    ("make sure test_notifications_router.py still passes"). If this class
    is needed a third time, that is the point to move it to conftest.py.

    httpx 0.28's stock ASGITransport (httpx/_transports/asgi.py) does
    `await self.app(scope, receive, send)` and only builds the Response
    object after that await returns -- it collects every response.body
    message into a list first. That makes it unusable here: the
    notification stream's generator never finishes on its own (it loops
    forever emitting keepalives until the client disconnects), so the
    stock transport's `await self.app(...)` would never return and
    `ac.stream(...)` would hang forever.

    This transport instead runs the ASGI app as a background task and
    streams `http.response.body` chunks out through a queue as they
    arrive, so `handle_async_request` can return a Response as soon as
    `http.response.start` shows up, while the app keeps running
    underneath. That is what lets this test interleave a read of the
    stream with the admin's POST /decide, which is the entire point of
    this gate.
    """

    def __init__(self, app) -> None:
        self.app = app
        self._tasks: set[asyncio.Task] = set()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": request.method,
            "headers": [(k.lower(), v) for k, v in request.headers.raw],
            "scheme": request.url.scheme,
            "path": request.url.path,
            "raw_path": request.url.raw_path.split(b"?")[0],
            "query_string": request.url.query,
            "server": (request.url.host, request.url.port),
            "client": ("testclient", 123),
            "root_path": "",
        }

        request_body = request.stream.__aiter__()
        request_complete = False

        async def receive():
            nonlocal request_complete
            if request_complete:
                await asyncio.sleep(3600)
            try:
                chunk = await request_body.__anext__()
            except StopAsyncIteration:
                request_complete = True
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.request", "body": chunk, "more_body": True}

        headers_ready = asyncio.Event()
        status_holder: dict = {}
        body_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def send(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
                status_holder["headers"] = message.get("headers", [])
                headers_ready.set()
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    await body_queue.put(body)
                if not message.get("more_body", False):
                    await body_queue.put(None)

        async def run_app():
            try:
                await self.app(scope, receive, send)
            except Exception:
                headers_ready.set()
                raise

        task = asyncio.ensure_future(run_app())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        await headers_ready.wait()

        return httpx.Response(
            status_holder.get("status", 500),
            headers=status_holder.get("headers", []),
            stream=_QueueByteStream(body_queue),
        )

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task


@pytest.mark.asyncio
async def test_approve_then_execute_then_sse_and_email(client, db_session, monkeypatch):
    """The phase 6 gate: one admin HTTP approval, one continuous flow,
    proving execution + email + SSE all happen off the same decision.

    Uses the `client` fixture only to perform two real HTTP logins (it
    already carries the `get_db -> db_session` dependency override this
    test needs) and to install that override on the shared `app` object.
    The actual approve/stream traffic runs over a second, `_LiveASGITransport`
    -backed AsyncClient against that same `app` instance, because the SSE
    half cannot be driven through the stock (or TestClient's synchronous)
    transport -- see `_LiveASGITransport`'s docstring.
    """
    from app.main import app

    transport_recorder = _AcceptingTransport()
    monkeypatch.setattr(email_module, "_transport", transport_recorder)
    monkeypatch.setattr(email_module, "allowlist_patterns", lambda: ["*@northstar.example"])

    requester_id, conversation_id = _hard_commit_requester_and_conversation(username="phase6-gate-employee")
    requester_headers = _login_existing(client, username="phase6-gate-employee")
    _admin, admin_headers = _login(client, db_session, username="phase6-gate-admin", role=Role.ADMIN)

    request = ApprovalRequest(
        conversation_id=conversation_id, task_id=None, requester_user_id=requester_id,
        action_type=ApprovalActionType.SEND_EMAIL,
        action_payload={
            "to_address": "ops@northstar.example",
            "subject": "VPN certificate rotation",
            "body": "Please rotate the VPN certificate for this user.",
        },
        justification="The user cannot connect and the certificate has expired.",
        risk_level=RiskLevel.MEDIUM, agent_summary="Email ops to rotate a VPN certificate.",
        status=ApprovalStatus.PENDING,
    )
    db_session.add(request)
    db_session.commit()

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        # The requester is listening on their notification stream before
        # the admin acts -- this is the "SSE" half of the gate.
        async with ac.stream(
            "GET", "/api/notifications/stream", headers=requester_headers,
        ) as stream:
            assert stream.status_code == 200
            lines = stream.aiter_lines()

            response = await ac.post(
                f"/api/admin/approvals/{request.id}/decide",
                json={"approve": True, "note": "Approved -- expired cert confirmed."},
                headers=admin_headers,
            )
            assert response.status_code == 200, response.text
            body = response.json()

            # Approve -> execute
            assert body["status"] == "executed"
            assert body["execution_result"]["email_status"] == "sent"

            # -> SSE
            event = await asyncio.wait_for(_next_event(lines), timeout=10)
            assert event["type"] == "approval_decided"
            assert "approved and executed" in event["title"]

    # -> email
    assert transport_recorder.sent == ["ops@northstar.example"]
    row = db_session.query(OutboundEmail).filter(
        OutboundEmail.approval_request_id == request.id,
    ).one()
    assert row.status is EmailStatus.SENT
    assert row.sent_at is not None
    assert row.approval_status_at_send in {ApprovalStatus.APPROVED, ApprovalStatus.EXECUTED}


async def _next_event(lines) -> dict:
    """Skips SSE keepalive comments (lines beginning with ':') and blanks --
    copied from test_notifications_router.py's identical helper. Not
    expected to matter at this test's timeouts (the keepalive interval is
    15s and every wait here is bounded well under that), but bounding every
    await with asyncio.wait_for and skipping non-data lines is what the
    task brief requires so a regression here fails fast instead of hanging
    the suite."""
    async for line in lines:
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError("stream ended before the decision event arrived")

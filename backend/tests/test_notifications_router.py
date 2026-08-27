from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import httpx
import pytest

from app.db.models import NotificationType, Role, User
from app.notifications import broker, service


def _login(client, db_session, *, username: str, role: Role = Role.EMPLOYEE) -> tuple[User, dict]:
    """Real login through the API, mirroring test_tickets_router.py's
    _login helper -- this project has no auth_headers_for_role /
    current_user_for_role fixtures, so tests get both the User row (to
    address notifications to) and the auth headers this same way."""
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


def _make_other_user(db_session) -> uuid.UUID:
    """notifications.user_id is a NOT NULL FK to users.id -- a bare
    uuid.uuid4() (as the brief's own draft used) trips ForeignKeyViolation.
    Same fix as test_tickets_router.py::_make_other_user: a real, unrelated
    User row through db_session so it lands in the same transaction."""
    other_id = uuid.uuid4()
    db_session.add(User(
        id=other_id, username=f"other-{other_id}", email=f"other-{other_id}@northstar.example",
        full_name="Other User", password_hash="x", role=Role.EMPLOYEE,
    ))
    db_session.commit()
    return other_id


def test_listing_returns_this_users_notifications_only(client, db_session):
    mine, headers = _login(client, db_session, username="notif-listing")
    other = _make_other_user(db_session)
    service.notify(db_session, user_id=mine.id, type=NotificationType.TICKET_CREATED, title="Mine", body="b")
    service.notify(db_session, user_id=other, type=NotificationType.TICKET_CREATED, title="Theirs", body="b")
    db_session.commit()

    body = client.get("/api/notifications", headers=headers).json()
    titles = [n["title"] for n in body]
    assert "Mine" in titles
    assert "Theirs" not in titles


def test_marking_read_sets_read_at(client, db_session):
    mine, headers = _login(client, db_session, username="notif-mark-read")
    row = service.notify(db_session, user_id=mine.id, type=NotificationType.TICKET_RESOLVED, title="T", body="b")
    db_session.commit()

    assert client.post(f"/api/notifications/{row.id}/read", headers=headers).status_code == 200
    remaining = client.get("/api/notifications?unread_only=true", headers=headers).json()
    assert [n["id"] for n in remaining] == []


def test_marking_someone_elses_notification_is_404(client, db_session):
    _mine, headers = _login(client, db_session, username="notif-not-mine")
    other = _make_other_user(db_session)
    row = service.notify(db_session, user_id=other, type=NotificationType.TICKET_RESOLVED, title="T", body="b")
    db_session.commit()
    response = client.post(f"/api/notifications/{row.id}/read", headers=headers)
    assert response.status_code == 404


class _QueueByteStream(httpx.AsyncByteStream):
    """Backs the httpx.Response returned by _LiveASGITransport: an async
    byte-iterable pulled off a queue that the running ASGI app keeps
    feeding, rather than a fixed, pre-collected buffer."""

    def __init__(self, queue: "asyncio.Queue[bytes | None]") -> None:
        self._queue = queue

    async def __aiter__(self):
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                return
            yield chunk


class _LiveASGITransport(httpx.AsyncBaseTransport):
    """httpx 0.28's stock ASGITransport (httpx/_transports/asgi.py) does
    `await self.app(scope, receive, send)` and only builds the Response
    object after that await returns -- it collects every response.body
    message into a list first. That makes it unusable for this test: the
    notification stream's generator never finishes on its own (it loops
    forever emitting keepalives until the client disconnects), so the
    stock transport's `await self.app(...)` would never return and
    `ac.stream(...)` would hang forever -- confirmed empirically before
    writing this class, not a guess.

    This transport instead runs the ASGI app as a background task and
    streams `http.response.body` chunks out through a queue as they
    arrive, so `handle_async_request` can return a Response as soon as
    `http.response.start` shows up, while the app keeps running
    underneath. That is what actually lets a test interleave a read of
    the stream with a `broker.publish(...)` call, which is the entire
    point of this test (TestClient's synchronous iteration can't do this
    either, per the test below).
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
                # Nothing more to give an app that keeps calling receive()
                # after the request body is exhausted (this endpoint never
                # does, but a well-behaved receive() must not raise here).
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
            finally:
                # Unblocks a caller still waiting on headers if the app
                # raised before ever calling send(), and terminates the
                # response body iterator if the app exits without an
                # explicit final `more_body: False` message.
                headers_ready.set()
                await body_queue.put(None)

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
async def test_the_stream_replays_unread_rows_then_delivers_live_events(client, db_session):
    """TestClient cannot do this: its synchronous iteration cannot interleave
    a publish with a read. This test drives the ASGI app through
    _LiveASGITransport instead of the stock httpx.ASGITransport, which
    cannot stream an endpoint that never finishes on its own -- see that
    class's docstring for why.

    Reuses the `client` fixture only to log in and to get its dependency
    override (app.db.session.get_db -> db_session) installed on the shared
    `app` object -- the actual streaming happens over a second, custom
    transport-backed async client against that same app.
    """
    from app.main import app

    mine, headers = _login(client, db_session, username="notif-stream")
    service.notify(db_session, user_id=mine.id, type=NotificationType.TICKET_CREATED, title="Backlog", body="b")
    db_session.commit()

    transport = _LiveASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        async with ac.stream("GET", "/api/notifications/stream", headers=headers) as response:
            assert response.status_code == 200
            lines = response.aiter_lines()

            replayed = await asyncio.wait_for(_next_event(lines), timeout=5)
            assert replayed["title"] == "Backlog"

            broker.publish(mine.id, {"type": "ticket_assigned", "id": str(uuid.uuid4()), "title": "Live", "body": "b"})
            live = await asyncio.wait_for(_next_event(lines), timeout=5)
            assert live["title"] == "Live"
    await transport.aclose()


async def _next_event(lines) -> dict:
    """Skips SSE keepalive comments (lines beginning with ':') and blanks."""
    async for line in lines:
        if line.startswith("data: "):
            return json.loads(line[len("data: "):])
    raise AssertionError("stream ended before an event arrived")

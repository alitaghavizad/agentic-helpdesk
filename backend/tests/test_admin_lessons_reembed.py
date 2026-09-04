"""admin_patch_lesson and admin_archive_lesson must re-embed on every
change (design spec 13: "edit (re-embedding on save)"), and roll back if
the embed fails (design decision D8) -- otherwise the DB and Chroma can
disagree about a lesson's content or status, which is exactly the
retrieval-poisoning risk archiving exists to prevent.
"""
from __future__ import annotations

from app.auth.security import hash_password
from app.db.models import Lesson, LessonConfidence, LessonStatus, Role, Run, RunStatus, RunTrigger, User


def _login_admin(client, db_session):
    user = User(
        username="lessonadmin", email="lessonadmin@northstar.example", full_name="Lesson Admin",
        password_hash=hash_password("Passw0rd!dev"), role=Role.ADMIN,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": "lessonadmin", "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_lesson(db_session, **overrides):
    """`Lesson.created_by_run_id` is a real NOT NULL FK to `runs.id` -- a bare
    `uuid.uuid4()` with no backing row trips ForeignKeyViolation on commit
    (this deviates from the brief, which passed one; see
    test_admin_mutations.py's own `_make_lesson` for the identical fix,
    already applied there for the same reason). A Run created through this
    same `db_session` lands in the same transaction, so the FK resolves and
    both roll back together at teardown."""
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
    db_session.add(run)
    db_session.commit()

    defaults = dict(
        title="t", category="c", content_md="original body", file_path="/x/y.md",
        applies_to=["a"], confidence=LessonConfidence.MEDIUM,
        status=LessonStatus.ACTIVE, created_by_run_id=run.id,
    )
    defaults.update(overrides)
    lesson = Lesson(**defaults)
    db_session.add(lesson)
    db_session.commit()
    return lesson


def test_patch_reembeds_with_the_new_content(client, db_session, monkeypatch):
    """Mocks at the RAG-backend level, one layer below writer.upsert_embedding
    itself, rather than replacing upsert_embedding wholesale -- that lets the
    real upsert_embedding run, including the Run it must guarantee is active
    before the (real or fake) backend's upsert() is ever called. Mocking
    upsert_embedding directly would bypass that guarantee entirely and could
    never catch its regression (see the get_current_run assertion below,
    and the identical rationale on test_learning_writer.py's
    _FakeRagBackend.upsert)."""
    import app.admin.router as admin_router_module
    from app.tracing.spans import get_current_run

    calls = []

    class _FakeBackend:
        async def upsert(self, collection, ids, documents, metadatas):
            assert get_current_run() is not None, "upsert() called with no active Run"
            calls.append(documents[0])

    monkeypatch.setattr(admin_router_module.writer, "get_rag_backend", lambda: _FakeBackend())

    headers = _login_admin(client, db_session)
    lesson = _make_lesson(db_session)

    resp = client.patch(f"/api/admin/lessons/{lesson.id}", json={"content_md": "revised body"}, headers=headers)

    assert resp.status_code == 200, resp.text
    assert calls == ["revised body"]


def test_archive_reembeds_with_archived_status(client, db_session, monkeypatch):
    import app.admin.router as admin_router_module
    from app.tracing.spans import get_current_run

    calls = []

    class _FakeBackend:
        async def upsert(self, collection, ids, documents, metadatas):
            assert get_current_run() is not None, "upsert() called with no active Run"
            calls.append(metadatas[0]["status"])

    monkeypatch.setattr(admin_router_module.writer, "get_rag_backend", lambda: _FakeBackend())

    headers = _login_admin(client, db_session)
    lesson = _make_lesson(db_session)

    resp = client.delete(f"/api/admin/lessons/{lesson.id}", headers=headers)

    assert resp.status_code == 200, resp.text
    assert calls == ["archived"]


def test_patch_rolls_back_and_returns_503_when_the_embed_fails(client, db_session, monkeypatch):
    import app.admin.router as admin_router_module

    class _FailingBackend:
        async def upsert(self, collection, ids, documents, metadatas):
            raise RuntimeError("chroma unreachable")

    monkeypatch.setattr(admin_router_module.writer, "get_rag_backend", lambda: _FailingBackend())

    headers = _login_admin(client, db_session)
    lesson = _make_lesson(db_session, content_md="original body")
    lesson_id = lesson.id

    resp = client.patch(f"/api/admin/lessons/{lesson_id}", json={"content_md": "this must not stick"}, headers=headers)

    assert resp.status_code == 503

    db_session.expire_all()
    stored = db_session.query(Lesson).filter(Lesson.id == lesson_id).one()
    assert stored.content_md == "original body"


def test_archive_rolls_back_and_returns_503_when_the_embed_fails(client, db_session, monkeypatch):
    """Mirrors test_patch_rolls_back_and_returns_503_when_the_embed_fails for
    the DELETE (archive) endpoint -- deleting admin_archive_lesson's
    db.rollback() call left all existing tests passing (nothing asserted the
    lesson's status was left untouched on a failed embed), so this pins that
    invariant directly."""
    import app.admin.router as admin_router_module

    class _FailingBackend:
        async def upsert(self, collection, ids, documents, metadatas):
            raise RuntimeError("chroma unreachable")

    monkeypatch.setattr(admin_router_module.writer, "get_rag_backend", lambda: _FailingBackend())

    headers = _login_admin(client, db_session)
    lesson = _make_lesson(db_session, status=LessonStatus.ACTIVE)
    lesson_id = lesson.id

    resp = client.delete(f"/api/admin/lessons/{lesson_id}", headers=headers)

    assert resp.status_code == 503

    db_session.expire_all()
    stored = db_session.query(Lesson).filter(Lesson.id == lesson_id).one()
    assert stored.status == LessonStatus.ACTIVE


def test_a_failed_embed_leaves_no_run_stuck_in_running(client, db_session, monkeypatch):
    """The no-leaked-RUNNING-run invariant: upsert_embedding's own Run (there
    is no ambient one for a standalone admin edit) must be finalized as
    ERROR, not left RUNNING, when the embed fails. Deleting the error-path
    _end_run_quietly call (replacing it with a bare `pass`) left every other
    test passing, since none of them queried the Run row itself -- this
    test does."""
    import app.admin.router as admin_router_module

    class _FailingBackend:
        async def upsert(self, collection, ids, documents, metadatas):
            raise RuntimeError("chroma unreachable")

    monkeypatch.setattr(admin_router_module.writer, "get_rag_backend", lambda: _FailingBackend())

    headers = _login_admin(client, db_session)
    lesson = _make_lesson(db_session)

    before_max_started_at = (
        db_session.query(Run.started_at).filter(Run.trigger == RunTrigger.LESSON_EDIT)
        .order_by(Run.started_at.desc()).first()
    )

    resp = client.patch(f"/api/admin/lessons/{lesson.id}", json={"content_md": "x"}, headers=headers)
    assert resp.status_code == 503

    # upsert_embedding commits its Run through its own connection
    # (get_sessionmaker()), separate from this test's db_session savepoint,
    # so it must be queried through a fresh connection to see it -- the same
    # cross-connection-visibility reasoning documented on cleanup_run in
    # tests/conftest.py.
    from app.db.session import get_sessionmaker

    Session = get_sessionmaker()
    with Session() as session:
        query = session.query(Run).filter(Run.trigger == RunTrigger.LESSON_EDIT)
        if before_max_started_at is not None:
            query = query.filter(Run.started_at > before_max_started_at[0])
        run = query.order_by(Run.started_at.desc()).first()
        assert run is not None, "expected upsert_embedding to have started its own Run"
        assert run.status == RunStatus.ERROR
        # Clean up: this Run was committed on its own connection, so it is
        # not covered by db_session's rollback at teardown.
        session.query(Run).filter(Run.id == run.id).delete()
        session.commit()

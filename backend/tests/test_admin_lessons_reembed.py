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
    import app.admin.router as admin_router_module

    calls = []

    async def _fake_upsert(lesson_row):
        calls.append(lesson_row.content_md)

    monkeypatch.setattr(admin_router_module.writer, "upsert_embedding", _fake_upsert)

    headers = _login_admin(client, db_session)
    lesson = _make_lesson(db_session)

    resp = client.patch(f"/api/admin/lessons/{lesson.id}", json={"content_md": "revised body"}, headers=headers)

    assert resp.status_code == 200, resp.text
    assert calls == ["revised body"]


def test_archive_reembeds_with_archived_status(client, db_session, monkeypatch):
    import app.admin.router as admin_router_module

    calls = []

    async def _fake_upsert(lesson_row):
        calls.append(lesson_row.status.value)

    monkeypatch.setattr(admin_router_module.writer, "upsert_embedding", _fake_upsert)

    headers = _login_admin(client, db_session)
    lesson = _make_lesson(db_session)

    resp = client.delete(f"/api/admin/lessons/{lesson.id}", headers=headers)

    assert resp.status_code == 200, resp.text
    assert calls == ["archived"]


def test_patch_rolls_back_and_returns_503_when_the_embed_fails(client, db_session, monkeypatch):
    import app.admin.router as admin_router_module

    async def _failing_upsert(lesson_row):
        raise RuntimeError("chroma unreachable")

    monkeypatch.setattr(admin_router_module.writer, "upsert_embedding", _failing_upsert)

    headers = _login_admin(client, db_session)
    lesson = _make_lesson(db_session, content_md="original body")
    lesson_id = lesson.id

    resp = client.patch(f"/api/admin/lessons/{lesson_id}", json={"content_md": "this must not stick"}, headers=headers)

    assert resp.status_code == 503

    db_session.expire_all()
    stored = db_session.query(Lesson).filter(Lesson.id == lesson_id).one()
    assert stored.content_md == "original body"

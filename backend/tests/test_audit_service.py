from __future__ import annotations

import inspect

import app.audit.service as audit_service
from app.audit.service import actor_from_principal, record_audit
from app.db.models import ActorType, AuditLog
from app.rbac.policy import Principal

_EMPLOYEE = Principal(kind="user", user_id="00000000-0000-0000-0000-000000000009", role="employee", clearance="standard", department="Engineering", employee_ref="EMP-009", helpdesk_ref=None)
_GUEST = Principal(kind="guest", user_id=None, role="guest", clearance=None, department=None, employee_ref=None, helpdesk_ref=None, guest_name="G", guest_email="g@example.com")


def test_record_audit_writes_a_row_with_every_field(db_session):
    row = record_audit(
        db_session, actor_type=ActorType.USER, actor_id="EMP-009", action="ticket.resolve",
        target_type="ticket", target_id="TCK-000001", payload={"resolution": "done"}, ip_address="10.0.0.1",
    )
    db_session.commit()

    stored = db_session.get(AuditLog, row.id)
    assert stored.actor_type == ActorType.USER
    assert stored.actor_id == "EMP-009"
    assert stored.action == "ticket.resolve"
    assert stored.target_type == "ticket"
    assert stored.target_id == "TCK-000001"
    assert stored.payload == {"resolution": "done"}
    assert stored.ip_address == "10.0.0.1"
    assert stored.created_at is not None


def test_record_audit_does_not_commit_so_the_row_shares_the_callers_transaction(db_session):
    """An audit row must never describe a mutation that was rolled back --
    so record_audit stages the row and leaves the commit to the caller."""
    record_audit(
        db_session, actor_type=ActorType.AGENT, actor_id=None, action="tool.denied",
        target_type="tool", target_id="search_knowledge",
    )
    db_session.rollback()

    assert db_session.query(AuditLog).filter(AuditLog.action == "tool.denied").count() == 0


def test_record_audit_defaults_payload_to_empty_dict(db_session):
    row = record_audit(
        db_session, actor_type=ActorType.SYSTEM, actor_id=None, action="x",
        target_type="t", target_id="1",
    )
    db_session.commit()
    assert db_session.get(AuditLog, row.id).payload == {}


def test_actor_from_principal_maps_user_and_guest():
    assert actor_from_principal(_EMPLOYEE) == (ActorType.USER, "00000000-0000-0000-0000-000000000009")
    assert actor_from_principal(_GUEST) == (ActorType.USER, "g@example.com")


_FORBIDDEN_VERBS = ("delete", "remove", "update", "purge", "drop", "destroy", "modify", "edit", "truncate", "erase", "clear")


def _names_suspicious_of_mutation(names):
    return [name for name in names if any(verb in name.lower() for verb in _FORBIDDEN_VERBS)]


def test_audit_module_has_no_update_or_delete_path():
    """Spec 5.4: audit_log is append-only -- 'no update or delete path exists
    in code'. record_audit() only ever adds+flushes a row; this is a
    structural guard against a mutation/removal path creeping into this
    module later.

    It flags any callable name reachable off `app.audit.service` -- a plain
    module-level function, an underscore-prefixed 'private' helper, a
    callable object bound to a module attribute, or a name imported from
    elsewhere and re-exported through this module -- as well as any method
    (including private ones) on a class that is *defined in* this module,
    whenever that name contains a mutation/removal verb such as "delete" or
    "update".

    What this does NOT catch: a delete/update path given an innocuous name
    with no telltale verb in it (e.g. a function literally called `handle`),
    or a mutating method reached through a class this module merely
    *imports* (e.g. `sqlalchemy.orm.Session.delete`) -- scanning arbitrary
    imported classes would flag library methods this module doesn't call as
    a delete path and defeat the point of the test. This is a backstop, not
    a substitute for code review.
    """
    offenders = []

    # Every name bound directly on the module -- functions, callables,
    # classes, anything -- regardless of leading underscore or where it was
    # originally defined (so a re-exported import is included, unlike a
    # check keyed on `obj.__module__`).
    for name, obj in vars(audit_service).items():
        if callable(obj) and _names_suspicious_of_mutation([name]):
            offenders.append(f"app.audit.service.{name}")

    # Every method (public or private) on a class actually defined in this
    # module -- not one merely imported into it.
    for name, obj in vars(audit_service).items():
        if inspect.isclass(obj) and obj.__module__ == audit_service.__name__:
            for meth_name, _meth in inspect.getmembers(obj, callable):
                if _names_suspicious_of_mutation([meth_name]):
                    offenders.append(f"{name}.{meth_name}")

    assert not offenders, f"possible update/delete path(s) found: {offenders}"

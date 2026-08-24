from __future__ import annotations

import re
import sys
from pathlib import Path

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.config import get_settings
from app.db.models import EscalationAuthority, Role, User
from app.db.session import get_sessionmaker
from app.rbac.policy import map_access_classification, map_escalation_authority

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DATASET_DIR = REPO_ROOT / "corporate_rag_dataset"

FIELD_RE = re.compile(r"^\*\*([^*:]+):\*\*\s*(.+?)\s*$", re.MULTILINE)
ACCESS_CLASS_RE = re.compile(r"Access classification:\s*([^.\n]+)")
NAME_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _parse_fields(text: str) -> dict[str, str]:
    return {key.strip(): value.strip() for key, value in FIELD_RE.findall(text)}


def _require_field(fields: dict[str, str], key: str, path: Path) -> str:
    """Extract a required field from parsed file content, with filename context in errors."""
    if key not in fields:
        raise ValueError(f"{path.name}: missing required field {key!r}")
    return fields[key]


def _parse_employee_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    fields = _parse_fields(text)
    name_match = NAME_RE.search(text)
    access_match = ACCESS_CLASS_RE.search(text)
    if not access_match:
        raise ValueError(f"{path.name}: no 'Access classification' line found")
    employee_id = _require_field(fields, "Employee ID", path)
    email = _require_field(fields, "Corporate email", path)
    return {
        "username": email.split("@")[0],
        "email": email,
        "full_name": name_match.group(1).strip() if name_match else employee_id,
        "role": Role.EMPLOYEE,
        "clearance": map_access_classification(access_match.group(1)),
        "department": fields.get("Department"),
        "employee_ref": employee_id,
        "location": fields.get("Location"),
    }


def _parse_helpdesk_file(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    fields = _parse_fields(text)
    name_match = NAME_RE.search(text)
    helpdesk_id = _require_field(fields, "Helpdesk ID", path)
    display_name = name_match.group(1).strip() if name_match else helpdesk_id
    username = display_name.lower().replace(" ", ".")
    escalation_authority_value = _require_field(fields, "Escalation authority", path)
    return {
        "username": username,
        "email": f"{username}@northstar.example",
        "full_name": display_name,
        "role": Role.HELPDESK,
        "helpdesk_ref": helpdesk_id,
        "specialization": fields.get("Primary specialization"),
        "escalation_authority": EscalationAuthority(
            map_escalation_authority(escalation_authority_value)
        ),
        "shift": fields.get("Shift"),
    }


def _upsert_user(session, *, password_hash: str, **fields) -> None:
    stmt = pg_insert(User).values(password_hash=password_hash, is_active=True, **fields)
    update_cols = {k: stmt.excluded[k] for k in fields if k != "username"}
    update_cols["password_hash"] = stmt.excluded.password_hash
    stmt = stmt.on_conflict_do_update(index_elements=["username"], set_=update_cols)
    session.execute(stmt)


def seed(session=None) -> dict[str, int]:
    from app.auth.security import hash_password

    settings = get_settings()
    owns_session = session is None
    if owns_session:
        session = get_sessionmaker()()

    counts = {"admin": 0, "employee": 0, "helpdesk": 0}
    try:
        _upsert_user(
            session,
            username=settings.admin_username,
            email=f"{settings.admin_username}@northstar.example",
            full_name="Administrator",
            role=Role.ADMIN,
            password_hash=hash_password(settings.admin_password),
        )
        counts["admin"] = 1

        seed_hash = hash_password(settings.seed_user_password)

        for path in sorted((DATASET_DIR / "employees").glob("EMP-*.md")):
            fields = _parse_employee_file(path)
            _upsert_user(session, password_hash=seed_hash, **fields)
            counts["employee"] += 1

        for path in sorted((DATASET_DIR / "helpdesk").glob("HD-*.md")):
            fields = _parse_helpdesk_file(path)
            _upsert_user(session, password_hash=seed_hash, **fields)
            counts["helpdesk"] += 1

        session.commit()
    finally:
        if owns_session:
            session.close()

    return counts


if __name__ == "__main__":
    result = seed()
    print(f"Seeded: {result}", file=sys.stderr)

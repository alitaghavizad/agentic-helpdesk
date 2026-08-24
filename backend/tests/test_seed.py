import pytest

from app.db.models import Clearance, EscalationAuthority, Role, User
from app.db.seed import seed, _parse_employee_file, _parse_helpdesk_file


def test_seed_creates_126_accounts(db_session):
    counts = seed(session=db_session)
    assert counts == {"admin": 1, "employee": 100, "helpdesk": 25}
    total = db_session.query(User).count()
    assert total == 126


def test_seed_maps_emp001_to_privileged_clearance(db_session):
    seed(session=db_session)
    emp001 = db_session.query(User).filter_by(employee_ref="EMP-001").one()
    assert emp001.clearance == Clearance.PRIVILEGED
    assert emp001.department == "Engineering"
    assert emp001.role == Role.EMPLOYEE
    assert emp001.full_name == "Narek Keller"


def test_seed_maps_hd001_specialization_and_escalation(db_session):
    seed(session=db_session)
    hd001 = db_session.query(User).filter_by(helpdesk_ref="HD-001").one()
    assert hd001.specialization == "Identity and Access Management"
    assert hd001.escalation_authority == EscalationAuthority.STANDARD
    assert hd001.role == Role.HELPDESK
    assert hd001.full_name == "Noah Taylor"


def test_seed_admin_account_created(db_session):
    seed(session=db_session)
    admin = db_session.query(User).filter_by(username="admin").one()
    assert admin.role == Role.ADMIN


def test_seed_is_idempotent(db_session):
    first = seed(session=db_session)
    second = seed(session=db_session)
    assert first == second
    total = db_session.query(User).count()
    assert total == 126


def test_parse_employee_file_missing_employee_id(tmp_path):
    """Verify that _parse_employee_file raises ValueError with filename context when Employee ID is missing."""
    malformed = tmp_path / "EMP-TEST.md"
    malformed.write_text(
        "# Test Employee\n"
        "**Corporate email:** test@example.com\n"
        "Access classification: Public\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        _parse_employee_file(malformed)
    assert "EMP-TEST.md" in str(exc_info.value)
    assert "Employee ID" in str(exc_info.value)


def test_parse_employee_file_missing_corporate_email(tmp_path):
    """Verify that _parse_employee_file raises ValueError with filename context when Corporate email is missing."""
    malformed = tmp_path / "EMP-TEST2.md"
    malformed.write_text(
        "# Test Employee\n"
        "**Employee ID:** EMP-999\n"
        "Access classification: Public\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        _parse_employee_file(malformed)
    assert "EMP-TEST2.md" in str(exc_info.value)
    assert "Corporate email" in str(exc_info.value)


def test_parse_helpdesk_file_missing_helpdesk_id(tmp_path):
    """Verify that _parse_helpdesk_file raises ValueError with filename context when Helpdesk ID is missing."""
    malformed = tmp_path / "HD-TEST.md"
    malformed.write_text(
        "# Test Helpdesk\n"
        "**Escalation authority:** Standard\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        _parse_helpdesk_file(malformed)
    assert "HD-TEST.md" in str(exc_info.value)
    assert "Helpdesk ID" in str(exc_info.value)


def test_parse_helpdesk_file_missing_escalation_authority(tmp_path):
    """Verify that _parse_helpdesk_file raises ValueError with filename context when Escalation authority is missing."""
    malformed = tmp_path / "HD-TEST2.md"
    malformed.write_text(
        "# Test Helpdesk\n"
        "**Helpdesk ID:** HD-999\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc_info:
        _parse_helpdesk_file(malformed)
    assert "HD-TEST2.md" in str(exc_info.value)
    assert "Escalation authority" in str(exc_info.value)

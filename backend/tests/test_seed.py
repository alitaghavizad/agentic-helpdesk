from app.db.models import Clearance, EscalationAuthority, Role, User
from app.db.seed import seed


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

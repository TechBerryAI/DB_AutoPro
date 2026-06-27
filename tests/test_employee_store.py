"""Employee authentication store tests."""

from desktop.employee_store import (
    authenticate_employee,
    register_employee,
    username_exists,
)


def test_authenticate_default_admin():
    session = authenticate_employee("admin", "admin123")
    assert session is not None
    assert session.username == "admin"
    assert session.full_name == "Administrator"


def test_register_and_login_employee(tmp_path, monkeypatch):
    from desktop import employee_store

    store_file = tmp_path / "employees.json"
    monkeypatch.setattr(employee_store, "EMPLOYEES_PATH", store_file)

    assert not username_exists("riya")
    register_employee(
        username="riya",
        password="secret12",
        full_name="Riya Sharma",
        email="riya@example.com",
    )
    assert username_exists("riya")

    session = authenticate_employee("riya", "secret12")
    assert session is not None
    assert session.full_name == "Riya Sharma"

    assert authenticate_employee("riya", "wrong") is None

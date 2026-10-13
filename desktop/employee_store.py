"""Persistent employee accounts for desktop login."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone

import bcrypt

from backend.core.paths import DATA_DIR
from backend.models.schemas import UserRole

EMPLOYEES_PATH = DATA_DIR / "employees.json"


@dataclass
class EmployeeSession:
    username: str
    full_name: str
    role: UserRole


@dataclass
class EmployeeRecord:
    username: str
    full_name: str
    hashed_password: str
    role: UserRole = UserRole.OPERATOR
    email: str = ""
    active: bool = True
    created_at: str = ""

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "full_name": self.full_name,
            "hashed_password": self.hashed_password,
            "role": self.role.value,
            "email": self.email,
            "active": self.active,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> EmployeeRecord:
        return cls(
            username=data["username"],
            full_name=data.get("full_name", data["username"]),
            hashed_password=data["hashed_password"],
            role=UserRole(data.get("role", UserRole.OPERATOR.value)),
            email=data.get("email", ""),
            active=data.get("active", True),
            created_at=data.get("created_at", ""),
        )


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def _ensure_store() -> None:
    if EMPLOYEES_PATH.exists():
        return
    EMPLOYEES_PATH.parent.mkdir(parents=True, exist_ok=True)
    bootstrap = {
        "employees": [
            EmployeeRecord(
                username="admin",
                full_name="Administrator",
                hashed_password=_hash_password("admin123"),
                role=UserRole.ADMIN,
                created_at=datetime.now(timezone.utc).isoformat(),
            ).to_dict(),
        ]
    }
    EMPLOYEES_PATH.write_text(json.dumps(bootstrap, indent=2), encoding="utf-8")


def load_employees() -> list[EmployeeRecord]:
    _ensure_store()
    data = json.loads(EMPLOYEES_PATH.read_text(encoding="utf-8"))
    return [EmployeeRecord.from_dict(item) for item in data.get("employees", [])]


def _save_employees(employees: list[EmployeeRecord]) -> None:
    EMPLOYEES_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {"employees": [e.to_dict() for e in employees]}
    EMPLOYEES_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def username_exists(username: str) -> bool:
    lookup = username.strip().lower()
    return any(e.username.lower() == lookup for e in load_employees())


def register_employee(
    *,
    username: str,
    password: str,
    full_name: str,
    email: str = "",
) -> EmployeeRecord:
    name = username.strip()
    if not name:
        raise ValueError("Username is required.")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters.")
    if username_exists(name):
        raise ValueError("Username is already taken.")

    record = EmployeeRecord(
        username=name,
        full_name=full_name.strip() or name,
        hashed_password=_hash_password(password),
        role=UserRole.OPERATOR,
        email=email.strip(),
        active=True,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    employees = load_employees()
    employees.append(record)
    _save_employees(employees)
    return record


def authenticate_employee(username: str, password: str) -> EmployeeSession | None:
    lookup = username.strip().lower()
    for employee in load_employees():
        if employee.username.lower() != lookup:
            continue
        if not employee.active:
            return None
        if not _verify_password(password, employee.hashed_password):
            return None
        return EmployeeSession(
            username=employee.username,
            full_name=employee.full_name,
            role=employee.role,
        )
    return None

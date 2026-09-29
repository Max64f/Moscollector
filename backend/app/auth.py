"""RBAC-заглушка: заголовок X-User-Login вместо LDAP/AD."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User

PERMISSIONS: dict[str, set[str]] = {
    "dispatcher": {
        "catalog:read",
        "events:read",
        "predictions:read",
        "predictions:decide",
        "tickets:write",
        "dashboard:read",
        "integrations:write",
        "import:write",
    },
    "analyst": {
        "catalog:read",
        "events:read",
        "predictions:read",
        "analytics:read",
        "analytics:write",
        "dashboard:read",
        "import:write",
    },
    "manager": {
        "catalog:read",
        "events:read",
        "predictions:read",
        "predictions:decide",
        "tickets:write",
        "dashboard:read",
        "analytics:read",
        "analytics:write",
        "import:write",
        "audit:read",
        "integrations:write",
    },
}


class CurrentUser(BaseModel):
    login: str
    role: str
    display_name: str

    def can(self, permission: str) -> bool:
        return permission in PERMISSIONS.get(self.role, set())


def get_current_user(
    db: Annotated[Session, Depends(get_db)],
    x_user_login: Annotated[str, Header(alias="X-User-Login")] = "dispatcher",
) -> CurrentUser:
    login = (x_user_login or "dispatcher").strip().lower()
    row = db.scalar(select(User).where(User.login == login))
    if row is None:
        raise HTTPException(status_code=401, detail=f"неизвестный пользователь: {login}")
    return CurrentUser(login=row.login, role=row.role, display_name=row.display_name)


def require(*permissions: str) -> Callable[..., CurrentUser]:
    def dependency(user: Annotated[CurrentUser, Depends(get_current_user)]) -> CurrentUser:
        missing = [item for item in permissions if not user.can(item)]
        if missing:
            raise HTTPException(
                status_code=403,
                detail=f"роль {user.role} не имеет права {', '.join(missing)}",
            )
        return user

    return dependency

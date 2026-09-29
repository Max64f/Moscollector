from __future__ import annotations

import json

from sqlalchemy.orm import Session

from app.models import AuditLog


def write_audit(db: Session, login: str | None, action: str, details: dict | str | None = None) -> None:
    payload = details
    if isinstance(details, dict):
        payload = json.dumps(details, ensure_ascii=False, default=str)
    db.add(AuditLog(user_login=login, action=action, details=payload))

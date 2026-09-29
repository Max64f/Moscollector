from fastapi import APIRouter, Depends

from app.auth import CurrentUser, get_current_user
from app.auth import PERMISSIONS

router = APIRouter(tags=["auth"])


@router.get("/auth/me")
def me(user: CurrentUser = Depends(get_current_user)) -> dict:
    return {
        "login": user.login,
        "role": user.role,
        "display_name": user.display_name,
        "permissions": sorted(PERMISSIONS.get(user.role, set())),
        "hint": "Передайте заголовок X-User-Login: dispatcher | analyst | manager",
    }

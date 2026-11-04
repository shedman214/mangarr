"""Login endpoints (public) and the Settings → Login controls (API key required)."""
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth
from .deps import replace_bootstrap_key

public = APIRouter(prefix="/auth", tags=["auth"])
router = APIRouter(prefix="/settings/login", tags=["settings"])


class LoginIn(BaseModel):
    username: str = Field(max_length=64)
    password: str = Field(max_length=auth.MAX_PASSWORD)


class LoginSettingsIn(BaseModel):
    enabled: bool
    username: str = Field(default="", max_length=64)
    new_password: str = Field(default="", max_length=auth.MAX_PASSWORD)
    current_password: str = Field(default="", max_length=auth.MAX_PASSWORD)


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").split(",")[0].strip() == "https"


def set_cookie(request: Request, response: Response, token: str) -> None:
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_SECONDS, httponly=True,
                        samesite="strict", secure=_secure(request), path="/")


@public.get("/status")
async def status(request: Request):
    return {"enabled": auth.enabled(), "authenticated": auth.valid_session(request.cookies.get(auth.COOKIE))}


@public.post("/login", status_code=204)
async def login(body: LoginIn, request: Request, response: Response):
    import asyncio
    client = request.client.host if request.client else "unknown"
    try:
        token = await asyncio.to_thread(auth.login, body.username, body.password, client)
    except auth.LoginError as exc:
        raise HTTPException(401, str(exc)) from exc
    set_cookie(request, response, token)


@public.post("/logout", status_code=204)
async def logout(request: Request, response: Response):
    auth.logout(request.cookies.get(auth.COOKIE))
    response.delete_cookie(auth.COOKIE, path="/")


@router.get("")
async def get_login_settings():
    return auth.describe()


@router.put("")
async def put_login_settings(body: LoginSettingsIn, request: Request, response: Response):
    import asyncio
    was_enabled = auth.enabled()
    try:
        token = await asyncio.to_thread(
            auth.configure, enable=body.enabled, username=body.username,
            new_password=body.new_password, current_password=body.current_password)
    except auth.LoginError as exc:
        raise HTTPException(400, str(exc)) from exc
    result = auth.describe()
    if token:
        set_cookie(request, response, token)
        if not was_enabled:
            # Everyone who opened the UI before has the old key; replace it.
            result["apiKey"] = replace_bootstrap_key()
    else:
        response.delete_cookie(auth.COOKIE, path="/")
    return result

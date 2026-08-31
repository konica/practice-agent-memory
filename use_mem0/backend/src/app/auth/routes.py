import secrets

import httpx
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse

from .google import authorization_url, exchange_code
from .session import (
    SESSION_COOKIE,
    SESSION_TTL,
    create_session,
    get_current_user,
    revoke_session,
)

router = APIRouter(prefix="/auth", tags=["auth"])

STATE_COOKIE = "oauth_state"


def _redirect_uri(request: Request) -> str:
    return str(request.url_for("auth_callback"))


@router.get("/login")
def login(request: Request) -> RedirectResponse:
    state = secrets.token_urlsafe(24)
    settings = request.app.state.settings
    url = authorization_url(settings.google_client_id, _redirect_uri(request), state)
    response = RedirectResponse(url)
    response.set_cookie(
        STATE_COOKIE, state, httponly=True, samesite="lax", max_age=600, secure=False
    )
    return response


@router.get("/callback", name="auth_callback")
def callback(
    request: Request,
    code: str,
    state: str,
    oauth_state: str | None = Cookie(default=None),
) -> RedirectResponse:
    if not oauth_state or not secrets.compare_digest(oauth_state, state):
        raise HTTPException(status_code=400, detail="Invalid OAuth state")

    settings = request.app.state.settings
    with httpx.Client(timeout=10) as http:
        identity = exchange_code(
            code,
            settings.google_client_id,
            settings.google_client_secret,
            _redirect_uri(request),
            http,
        )

    pool = request.app.state.pool
    with pool.connection() as conn:
        conn.execute(
            """
            INSERT INTO users (sub, email, name, picture) VALUES (%s, %s, %s, %s)
            ON CONFLICT (sub) DO UPDATE
              SET email = EXCLUDED.email, name = EXCLUDED.name, picture = EXCLUDED.picture
            """,
            (identity.sub, identity.email, identity.name, identity.picture),
        )

    cookie = create_session(pool, identity.sub, settings.session_secret)
    response = RedirectResponse(settings.frontend_origin)
    response.delete_cookie(STATE_COOKIE)
    response.set_cookie(
        SESSION_COOKIE,
        cookie,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=int(SESSION_TTL.total_seconds()),
    )
    return response


@router.get("/me")
def me(request: Request, user_sub: str = Depends(get_current_user)) -> JSONResponse:
    with request.app.state.pool.connection() as conn:
        row = conn.execute(
            "SELECT sub, email, name, picture FROM users WHERE sub = %s", (user_sub,)
        ).fetchone()
    return JSONResponse(dict(row))


@router.post("/logout")
def logout(
    request: Request, response: Response, session: str | None = Cookie(default=None)
):
    if session:
        revoke_session(
            request.app.state.pool, session, request.app.state.settings.session_secret
        )
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}

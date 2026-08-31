import uuid
from datetime import datetime, timedelta, timezone

from fastapi import Cookie, HTTPException, Request
from itsdangerous import BadData, URLSafeSerializer

SESSION_COOKIE = "session"
SESSION_TTL = timedelta(days=14)
_SALT = "session"


def _serializer(secret: str) -> URLSafeSerializer:
    return URLSafeSerializer(secret, salt=_SALT)


def _unsign(cookie_value: str, secret: str) -> uuid.UUID | None:
    """Return the session id a cookie carries, or None if it isn't trustworthy.

    A signature that verifies only proves we minted the payload; it still has to
    be a session id we could look up, so the UUID parse is part of the check.
    """
    try:
        return uuid.UUID(_serializer(secret).loads(cookie_value))
    except (BadData, ValueError, TypeError, AttributeError):
        return None


def create_session(pool, user_sub: str, secret: str) -> str:
    session_id = uuid.uuid4()
    expires_at = datetime.now(timezone.utc) + SESSION_TTL
    with pool.connection() as conn:
        conn.execute(
            "INSERT INTO auth_sessions (id, user_sub, expires_at) VALUES (%s, %s, %s)",
            (session_id, user_sub, expires_at),
        )
    return _serializer(secret).dumps(str(session_id))


def resolve_session(pool, cookie_value: str, secret: str) -> str | None:
    session_id = _unsign(cookie_value, secret)
    if session_id is None:
        return None
    with pool.connection() as conn:
        row = conn.execute(
            """
            SELECT user_sub FROM auth_sessions
            WHERE id = %s AND revoked_at IS NULL AND expires_at > now()
            """,
            (session_id,),
        ).fetchone()
    return row["user_sub"] if row else None


def revoke_session(pool, cookie_value: str, secret: str) -> None:
    session_id = _unsign(cookie_value, secret)
    if session_id is None:
        return
    with pool.connection() as conn:
        conn.execute(
            "UPDATE auth_sessions SET revoked_at = now() WHERE id = %s", (session_id,)
        )


def get_current_user(request: Request, session: str | None = Cookie(default=None)) -> str:
    """FastAPI dependency: returns the authenticated user's Google `sub`, or 401."""
    if not session:
        raise HTTPException(status_code=401, detail="Not authenticated")
    user_sub = resolve_session(
        request.app.state.pool, session, request.app.state.settings.session_secret
    )
    if user_sub is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_sub

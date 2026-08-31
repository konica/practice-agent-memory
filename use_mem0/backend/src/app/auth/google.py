from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPES = "openid email profile"


@dataclass(frozen=True)
class GoogleIdentity:
    """The claims we keep from a verified Google id token.

    `sub` is the user key and the mem0 `user_id`; `email` is display only,
    because a user can change it.
    """

    sub: str
    email: str
    name: str | None
    picture: str | None


def authorization_url(client_id: str, redirect_uri: str, state: str) -> str:
    return (
        AUTH_ENDPOINT
        + "?"
        + urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "response_type": "code",
                "scope": SCOPES,
                "state": state,
                "access_type": "online",
                "prompt": "select_account",
            }
        )
    )


def exchange_code(
    code: str, client_id: str, client_secret: str, redirect_uri: str, http: httpx.Client
) -> GoogleIdentity:
    response = http.post(
        TOKEN_ENDPOINT,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    response.raise_for_status()
    raw_id_token = response.json()["id_token"]
    claims = id_token.verify_oauth2_token(
        raw_id_token, google_requests.Request(), client_id
    )
    return GoogleIdentity(
        sub=claims["sub"],
        email=claims["email"],
        name=claims.get("name"),
        picture=claims.get("picture"),
    )

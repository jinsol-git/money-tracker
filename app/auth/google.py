"""Google 로그인(OpenID Connect, Authorization Code + PKCE)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx
import jwt

from app.security.crypto import b64url_encode, constant_time_equals

AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
JWKS_ENDPOINT = "https://www.googleapis.com/oauth2/v3/certs"
ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class GoogleAuthError(Exception):
    """로그인 실패. 메시지에는 토큰이나 코드를 넣지 않는다."""


@dataclass(frozen=True)
class GoogleIdentity:
    sub: str
    email: str
    email_verified: bool


def code_challenge(verifier: str) -> str:
    return b64url_encode(hashlib.sha256(verifier.encode()).digest())


class GoogleClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        transport: httpx.BaseTransport | None = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._redirect_uri = redirect_uri
        self._http = httpx.Client(timeout=10, transport=transport)

    def authorization_url(self, state: str, nonce: str, verifier: str) -> str:
        return f"{AUTHORIZATION_ENDPOINT}?" + urlencode(
            {
                "client_id": self._client_id,
                "redirect_uri": self._redirect_uri,
                "response_type": "code",
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "code_challenge": code_challenge(verifier),
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )

    def exchange_code(self, code: str, verifier: str) -> str:
        try:
            response = self._http.post(
                TOKEN_ENDPOINT,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "code_verifier": verifier,
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                    "redirect_uri": self._redirect_uri,
                },
            )
            response.raise_for_status()
            return response.json()["id_token"]
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise GoogleAuthError("인증 코드를 교환하지 못했습니다.") from exc

    def verify_id_token(self, id_token: str, nonce: str) -> GoogleIdentity:
        try:
            header = jwt.get_unverified_header(id_token)
            if header.get("alg") != "RS256":
                raise GoogleAuthError("허용하지 않는 서명 방식입니다.")
            response = self._http.get(JWKS_ENDPOINT)
            response.raise_for_status()
            matching = [key for key in response.json()["keys"] if key.get("kid") == header.get("kid")]
            if len(matching) != 1:
                raise GoogleAuthError("서명 키를 찾을 수 없습니다.")
            claims = jwt.decode(
                id_token,
                jwt.PyJWK(matching[0]).key,
                algorithms=["RS256"],
                audience=self._client_id,
                issuer=list(ISSUERS),
                options={"require": ["exp", "iat", "sub", "aud", "iss"]},
                leeway=30,
            )
        except (jwt.PyJWTError, httpx.HTTPError, KeyError, ValueError) as exc:
            raise GoogleAuthError("ID 토큰을 검증하지 못했습니다.") from exc

        if not constant_time_equals(str(claims.get("nonce", "")), nonce):
            raise GoogleAuthError("nonce가 맞지 않습니다.")
        return GoogleIdentity(
            sub=str(claims["sub"]),
            email=str(claims.get("email", "")),
            email_verified=claims.get("email_verified") is True,
        )

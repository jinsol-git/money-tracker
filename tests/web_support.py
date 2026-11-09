"""웹 테스트 도구: 멈춘 시계, 가짜 Google 서버, 브라우저 흉내."""

from __future__ import annotations

import base64
import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import jwt
import pyotp
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.auth.google import JWKS_ENDPOINT, TOKEN_ENDPOINT
from app.auth.gate import sign_body
from app.config import Settings

OWNER_EMAIL = "owner@example.com"
OWNER_SUB = "google-sub-owner"
CLIENT_ID = "test-client-id"
HMAC_KEY = "h" * 40
BASE_URL = "https://testserver"
CSRF_RE = re.compile(r'name="csrf_token" value="([^"]*)"')
SECRET_RE = re.compile(r'data-secret="([A-Z2-7]+)"')
CODE_RE = re.compile(r"<li><code>([0-9a-f\-]+)</code></li>")

# 실제 메일의 Authentication-Results와 같은 구조다.
GOOD_AUTH_RESULTS = (
    "mx.google.com; dkim=pass header.i=@banksalad.com header.s=abc header.b=AAAAAAAA; "
    'dkim=pass header.i=@amazonses.com header.s=def header.b="BBBB//BB"; '
    "spf=pass (google.com: domain of bounce@ap-northeast-2.amazonses.com designates 192.0.2.1 as permitted sender) "
    "smtp.mailfrom=bounce@ap-northeast-2.amazonses.com; "
    "dmarc=pass (p=NONE sp=NONE dis=NONE) header.from=banksalad.com"
)


def make_settings() -> Settings:
    return Settings(
        database_url="postgresql://unused",
        master_key=bytes(range(32)),
        ingest_hmac_key=HMAC_KEY,
        google_client_id=CLIENT_ID,
        google_client_secret="client-secret",
        owner_email=OWNER_EMAIL,
        base_url=BASE_URL,
    )


@dataclass
class FrozenClock:
    current: datetime = field(default_factory=lambda: datetime(2026, 11, 5, 3, 0, 0, tzinfo=UTC))

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs: Any) -> None:
        self.current += timedelta(**kwargs)


class FakeGoogle:
    """토큰·JWKS 엔드포인트를 흉내 낸다. 앱의 실제 서명 검증 경로를 그대로 거치게 한다."""

    def __init__(self) -> None:
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "test-kid"
        self.nonce = ""
        self.challenge = ""
        self.claims: dict[str, Any] = {}
        self.token_override: str | None = None
        self.fail_token_endpoint = False
        self.verifier_ok: bool | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def default_claims(self) -> dict[str, Any]:
        now = int(datetime.now(UTC).timestamp())
        return {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "sub": OWNER_SUB,
            "email": OWNER_EMAIL,
            "email_verified": True,
            "nonce": self.nonce,
            "iat": now,
            "exp": now + 3600,
        }

    def mint(self, **overrides: Any) -> str:
        claims = {**self.default_claims(), **self.claims, **overrides}
        claims = {key: value for key, value in claims.items() if value is not None}
        return jwt.encode(claims, self.private_key, algorithm="RS256", headers={"kid": self.kid})

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == TOKEN_ENDPOINT:
            if self.fail_token_endpoint:
                return httpx.Response(400, json={"error": "invalid_grant"})
            form = parse_qs(request.content.decode())
            verifier = form["code_verifier"][0]
            digest = hashlib.sha256(verifier.encode()).digest()
            self.verifier_ok = base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == self.challenge
            return httpx.Response(200, json={"id_token": self.token_override or self.mint()})
        if url == JWKS_ENDPOINT:
            jwk = RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
            return httpx.Response(200, json={"keys": [{**jwk, "kid": self.kid, "alg": "RS256", "use": "sig"}]})
        return httpx.Response(404)


class Browser:
    """기기 하나. 쿠키를 따로 갖는다."""

    def __init__(self, app, google: FakeGoogle, clock: FrozenClock):
        self.client = TestClient(app, base_url=BASE_URL, follow_redirects=False)
        self.google = google
        self.clock = clock

    def start_google(self) -> dict[str, str]:
        response = self.client.get("/auth/google")
        assert response.status_code == 303
        query = parse_qs(urlsplit(response.headers["location"]).query)
        self.google.nonce = query["nonce"][0]
        self.google.challenge = query["code_challenge"][0]
        return {name: values[0] for name, values in query.items()}

    def google_callback(self, query: dict[str, str] | None = None, code: str = "auth-code") -> httpx.Response:
        params = query or self.start_google()
        return self.client.get("/auth/callback?" + urlencode({"state": params["state"], "code": code}))

    def csrf(self, path: str) -> str:
        response = self.client.get(path)
        match = CSRF_RE.search(response.text)
        assert match, f"{path}에 CSRF 토큰이 없습니다 (상태 {response.status_code})"
        return match.group(1)

    def post(self, path: str, form_page: str | None = None, **fields: Any) -> httpx.Response:
        token = self.csrf(form_page or path)
        return self.client.post(path, data={"csrf_token": token, **fields})

    def enroll(self) -> tuple[str, list[str]]:
        page = self.client.get("/auth/enroll")
        seed = SECRET_RE.search(page.text).group(1)
        response = self.client.post(
            "/auth/enroll",
            data={"csrf_token": CSRF_RE.search(page.text).group(1), "code": self.otp(seed)},
        )
        assert response.status_code == 200, response.status_code
        return seed, CODE_RE.findall(response.text)

    def otp(self, seed: str, offset_steps: int = 0) -> str:
        return pyotp.TOTP(seed).at(int(self.clock().timestamp()) + offset_steps * 30)

    def login(self, seed: str) -> httpx.Response:
        assert self.google_callback().headers["location"] == "/auth/mfa"
        self.clock.advance(seconds=30)  # 직전 코드 재사용 방지에 걸리지 않게 다음 단계로 넘어간다
        return self.post("/auth/mfa", code=self.otp(seed))

    def signed_post(self, path: str, payload: Any, *, timestamp: int | None = None, key: str = HMAC_KEY) -> httpx.Response:
        body = json.dumps(payload).encode()
        stamp = str(timestamp if timestamp is not None else int(self.clock().timestamp()))
        return self.client.post(
            path,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-Ledger-Timestamp": stamp,
                "X-Ledger-Signature": sign_body(key, stamp, path, body),
            },
        )

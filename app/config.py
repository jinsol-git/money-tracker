"""환경변수 설정. 값은 Vercel의 Sensitive 환경변수에 두고 저장소에는 넣지 않는다."""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """설정 오류. 메시지에는 변수 이름만 넣고 값은 넣지 않는다."""


@dataclass(frozen=True)
class Settings:
    database_url: str
    master_key: bytes
    ingest_hmac_key: str
    google_client_id: str
    google_client_secret: str
    owner_email: str
    base_url: str
    secure_cookies: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        required = (
            "DATABASE_URL",
            "MASTER_KEY",
            "INGEST_HMAC_KEY",
            "GOOGLE_CLIENT_ID",
            "GOOGLE_CLIENT_SECRET",
            "OWNER_EMAIL",
            "BASE_URL",
        )
        missing = [name for name in required if not os.environ.get(name)]
        if missing:
            raise ConfigError(f"환경변수가 없습니다: {', '.join(missing)}")

        base_url = os.environ["BASE_URL"].rstrip("/")
        # 로컬 개발 전용 설정이다. Vercel에서는 값이 있어도 무시한다.
        secure = bool(os.environ.get("VERCEL")) or os.environ.get("INSECURE_LOCAL_COOKIES") != "1"
        if secure and not base_url.startswith("https://"):
            raise ConfigError("BASE_URL은 https여야 합니다.")
        return cls(
            database_url=os.environ["DATABASE_URL"],
            master_key=parse_master_key(os.environ["MASTER_KEY"]),
            ingest_hmac_key=parse_hmac_key(os.environ["INGEST_HMAC_KEY"]),
            google_client_id=os.environ["GOOGLE_CLIENT_ID"],
            google_client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
            owner_email=os.environ["OWNER_EMAIL"],
            base_url=base_url,
            secure_cookies=secure,
        )

    @property
    def redirect_uri(self) -> str:
        return f"{self.base_url}/auth/callback"

    @property
    def session_cookie(self) -> str:
        return "__Host-session" if self.secure_cookies else "session"

    @property
    def flow_cookie(self) -> str:
        return "__Host-flow" if self.secure_cookies else "flow"


def parse_master_key(value: str) -> bytes:
    try:
        key = bytes.fromhex(value.strip())
    except ValueError as exc:
        raise ConfigError("MASTER_KEY는 16진수 문자열이어야 합니다.") from exc
    if len(key) != 32:
        raise ConfigError("MASTER_KEY는 32바이트(16진수 64자)여야 합니다.")
    return key


def parse_hmac_key(value: str) -> str:
    key = value.strip()
    if len(key) < 32:
        raise ConfigError("INGEST_HMAC_KEY는 32자 이상이어야 합니다.")
    return key

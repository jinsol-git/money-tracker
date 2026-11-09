"""Google Authenticator 호환 OTP(RFC 6238)와 복구 코드, 실패 잠금."""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timedelta
from math import ceil

import pyotp
import segno
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import Settings
from app.ops_models import Owner, RecoveryCode
from app.security.crypto import constant_time_equals, keyed_hash

FREE_ATTEMPTS = 2  # 두 번까지는 오타로 보고 기다리게 하지 않는다
# 연속 실패가 늘수록 기다리는 시간도 늘어난다. 끝까지 맞히지 못하면 마지막 값이 계속 적용된다.
# 상한을 두는 이유는 Google 계정을 뺏은 사람이 주인을 며칠씩 잠그지 못하게 하기 위해서다.
LOCK_SCHEDULE = (
    timedelta(seconds=30),
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
)
RECOVERY_CODE_COUNT = 10
STEP_SECONDS = 30


def new_seed() -> str:
    return pyotp.random_base32()


def provisioning_uri(seed: str, account: str) -> str:
    return pyotp.TOTP(seed).provisioning_uri(name=account, issuer_name="Ledger")


def qr_svg(uri: str) -> str:
    """외부 서비스 없이 QR을 만든다. 시드가 밖으로 나가지 않아야 하기 때문이다."""
    return segno.make(uri, error="m").svg_inline(scale=5, dark="#000", light="#fff")


def verify(seed: str, code: str, now: datetime, last_step: int | None) -> int | None:
    """맞으면 시간 단계를 돌려준다. 시계 오차를 앞뒤 1단계 허용하고, 이미 쓴 단계는 받지 않는다."""
    if not re.fullmatch(r"\d{6}", code):
        return None
    current = int(now.timestamp()) // STEP_SECONDS
    totp = pyotp.TOTP(seed)
    for step in (current - 1, current, current + 1):
        if last_step is not None and step <= last_step:
            continue
        if constant_time_equals(totp.at(step * STEP_SECONDS), code):
            return step
    return None


def lock_owner(db: Session) -> Owner | None:
    """행을 잠근 채 읽는다. 동시에 보낸 코드들이 실패 횟수와 재사용 방지를 건너뛰지 못하게 한다."""
    return db.get(Owner, 1, with_for_update=True, populate_existing=True)


def is_locked(owner: Owner, now: datetime) -> bool:
    return owner.locked_until is not None and owner.locked_until > now


def wait_text(owner: Owner, now: datetime) -> str:
    """남은 대기 시간. 초 단위까지 올림해 끝나기 전에 다시 시도하는 일을 줄인다."""
    if not is_locked(owner, now):
        return ""
    seconds = ceil((owner.locked_until - now).total_seconds())
    hours, rest = divmod(seconds, 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}시간 {minutes}분" if minutes else f"{hours}시간"
    if minutes:
        return f"{minutes}분 {seconds}초" if seconds else f"{minutes}분"
    return f"{seconds}초"


def register_failure(owner: Owner, now: datetime) -> None:
    owner.failed_attempts += 1
    over = owner.failed_attempts - FREE_ATTEMPTS
    if over > 0:
        owner.locked_until = now + LOCK_SCHEDULE[min(over, len(LOCK_SCHEDULE)) - 1]


def register_success(owner: Owner) -> None:
    owner.failed_attempts = 0
    owner.locked_until = None


def _normalize_recovery_code(code: str) -> str:
    return re.sub(r"[^0-9a-z]", "", code.lower())


def _recovery_hash(settings: Settings, code: str) -> str:
    return keyed_hash(settings.master_key, "recovery-code", _normalize_recovery_code(code).encode())


def issue_recovery_codes(db: Session, settings: Settings, now: datetime) -> list[str]:
    """기존 코드를 모두 무효화하고 새로 발급한다. 평문은 이 응답에서만 볼 수 있다."""
    db.execute(update(RecoveryCode).where(RecoveryCode.used_at.is_(None)).values(used_at=now))
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(10)  # 80비트
        code = "-".join(raw[i : i + 5] for i in range(0, len(raw), 5))
        db.add(RecoveryCode(code_hash=_recovery_hash(settings, code), created_at=now))
        codes.append(code)
    db.flush()
    return codes


def consume_recovery_code(db: Session, settings: Settings, code: str, now: datetime) -> bool:
    record = db.scalar(
        select(RecoveryCode).where(
            RecoveryCode.code_hash == _recovery_hash(settings, code),
            RecoveryCode.used_at.is_(None),
        )
    )
    if record is None:
        return False
    record.used_at = now
    return True


def remaining_recovery_codes(db: Session) -> int:
    return len(db.scalars(select(RecoveryCode.id).where(RecoveryCode.used_at.is_(None))).all())

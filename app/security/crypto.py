"""MASTER_KEY에서 용도별 키를 파생해 쓴다. 한 용도의 키가 다른 용도에 쓰이지 않게 하기 위해서다."""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

NONCE_BYTES = 12


class CryptoError(Exception):
    """복호화 실패. 키가 다르거나 데이터가 변조되었다."""


def derive_key(master_key: bytes, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=f"ledger:{purpose}".encode()).derive(master_key)


def seal(master_key: bytes, purpose: str, plaintext: bytes) -> bytes:
    """AES-256-GCM. 용도를 인증 데이터로 묶어 다른 용도로 꺼낼 수 없게 한다."""
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(derive_key(master_key, purpose)).encrypt(nonce, plaintext, purpose.encode())
    return nonce + ciphertext


def unseal(master_key: bytes, purpose: str, blob: bytes) -> bytes:
    nonce, ciphertext = blob[:NONCE_BYTES], blob[NONCE_BYTES:]
    try:
        return AESGCM(derive_key(master_key, purpose)).decrypt(nonce, ciphertext, purpose.encode())
    except (InvalidTag, ValueError) as exc:
        raise CryptoError("복호화에 실패했습니다.") from exc


def keyed_hash(master_key: bytes, purpose: str, value: bytes) -> str:
    return hmac.new(derive_key(master_key, purpose), value, hashlib.sha256).hexdigest()


def constant_time_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left.encode(), right.encode())


def b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))

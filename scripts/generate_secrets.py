#!/usr/bin/env python3
"""MASTER_KEY와 INGEST_HMAC_KEY를 만들어 터미널에 출력한다. 파일에는 저장하지 않는다.

    python3 scripts/generate_secrets.py
"""

import secrets

print("아래 두 값을 Vercel 환경변수에 넣고, 비밀번호 관리자에도 저장하세요.")
print("MASTER_KEY를 잃으면 백업을 복원할 수 없습니다.")
print()
print(f"MASTER_KEY={secrets.token_hex(32)}")
print(f"INGEST_HMAC_KEY={secrets.token_urlsafe(48)}")

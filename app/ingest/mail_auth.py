"""메일이 정말 뱅크샐러드가 나에게 보낸 것인지 판단한다.

banksalad.com은 DMARC 정책이 p=none이라 발신 주소가 위조되어도 메일이 도착할 수 있다.
그래서 Gmail이 맨 위에 붙인 Authentication-Results의 DKIM·DMARC 결과를 본다.

DKIM만으로는 부족하다. 공격자가 자기 뱅크샐러드 내보내기 메일을 서명 그대로 나에게
다시 보내면 인증을 통과한다. banksalad.com의 서명은 To와 From을 포함하므로(실제 메일로
확인), To가 내 주소이고 하나뿐인지 확인하면 막을 수 있다. 서명은 맨 아래 헤더를 기준으로
검증되므로 위쪽에 To를 하나 더 끼워 넣는 공격을 막으려면 개수까지 봐야 한다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from email.utils import getaddresses

SENDER_ADDRESS = "export-noreply@banksalad.com"
SENDER_DOMAIN = "banksalad.com"
AUTHSERV_ID = "mx.google.com"
_DOMAIN = re.escape(SENDER_DOMAIN)
_DKIM_PASS = re.compile(rf"^dkim=pass\b.*\bheader\.i=@{_DOMAIN}(?=\s|$)", re.IGNORECASE)
_DMARC_PASS = re.compile(rf"^dmarc=pass\b.*\bheader\.from={_DOMAIN}(?=\s|$)", re.IGNORECASE)


def _strip_comments_and_quotes(text: str) -> str:
    """괄호 주석과 따옴표 안의 값은 발신자가 정할 수 있어, 구분자로 해석하기 전에 지운다.

    닫히지 않은 따옴표나 괄호가 있으면 뒤쪽이 모두 지워져 인증 실패로 끝난다.
    """
    kept: list[str] = []
    depth = 0
    quoted = escaped = False
    for character in text:
        if escaped:
            escaped = False
        elif quoted:
            if character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
        elif depth > 0:
            if character == "\\":
                escaped = True
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
        elif character == '"':
            quoted = True
        elif character == "(":
            depth = 1
        else:
            kept.append(character)
    return "".join(kept)


def signature_is_valid(authentication_results: str) -> bool:
    text = " ".join(_strip_comments_and_quotes(authentication_results).split())
    head, separator, rest = text.partition(";")
    if not separator or head.strip().split(" ")[0].casefold() != AUTHSERV_ID:
        return False
    clauses = [clause.strip() for clause in rest.split(";")]
    return any(_DKIM_PASS.search(clause) for clause in clauses) and any(
        _DMARC_PASS.search(clause) for clause in clauses
    )


def _single_address(headers: Sequence[str]) -> str | None:
    if len(headers) != 1:
        return None
    addresses = getaddresses(list(headers))
    if len(addresses) != 1 or not addresses[0][1]:
        return None
    return addresses[0][1].casefold()


def rejection_reason(
    authentication_results: str,
    to_headers: Sequence[str],
    from_headers: Sequence[str],
    owner_email: str,
) -> str | None:
    """문제가 없으면 None을 돌려준다."""
    if not signature_is_valid(authentication_results):
        return "signature"
    if _single_address(from_headers) != SENDER_ADDRESS:
        return "sender"
    if _single_address(to_headers) != owner_email.casefold():
        return "recipient"
    return None

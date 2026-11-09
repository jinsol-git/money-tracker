#!/usr/bin/env python3
"""뱅크샐러드 내보내기 ZIP의 '구조'만 출력한다.

금액, 거래 내용, 메모, 이름은 출력하지 않는다. 출력은 건수, 열 이름, 형식뿐이고
결제수단 이름의 숫자는 #으로 가린다. 비밀번호는 실행 중에 터미널에서만 입력받는다.

    ~/.local/bin/uv run python -I scripts/inspect_export.py <ZIP 경로>
"""

from __future__ import annotations

import getpass
import io
import re
import struct
import sys
import traceback
import unicodedata
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, TextIO

import msoffcrypto
import openpyxl
import pyzipper
from openpyxl.utils import get_column_letter
from openpyxl.xml import DEFUSEDXML

MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_UNPACKED_BYTES = 50 * 1024 * 1024
OLE_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
ZIP_MAGIC = b"PK\x03\x04"
WINZIP_AES_METHOD = 99
WINZIP_AES_EXTRA_ID = 0x9901
AES_STRENGTH_BITS = {1: 128, 2: 192, 3: 256}

KNOWN_COLUMNS = {"날짜", "시간", "타입", "대분류", "소분류", "내용", "금액", "화폐", "결제수단", "메모"}

# 뱅샐현황처럼 자유 형식인 시트에서 그대로 출력해도 되는 구조용 단어
SAFE_LABELS = {
    "고객정보", "이름", "성명", "성별", "나이", "만 나이", "연령", "생년월일", "직업", "신용점수",
    "이메일", "연락처", "현금흐름", "현금흐름현황", "현금흐름 현황", "재무현황", "재무 현황",
    "보험현황", "보험 현황", "투자현황", "투자 현황", "대출현황", "대출 현황", "항목", "구분", "분류",
    "상품명", "상품", "금융사", "기관", "기관명", "금액", "잔액", "합계", "총계", "소계", "평균", "월평균",
    "월합계", "총합계", "수입", "지출", "이체", "저축", "순수입", "순수익", "총수입", "총지출", "월수입",
    "월지출", "자산", "부채", "총자산", "총부채", "순자산", "현금", "자유입출금", "자유입출금 자산",
    "신탁", "신탁 자산", "저축성", "저축성 자산", "투자성", "투자성 자산", "부동산", "동산", "전월세",
    "전월세 보증금", "기타", "기타 자산", "기타 부채", "연금", "연금 자산", "카드", "카드 대금", "카드대금",
    "대출", "단기 대출", "장기 대출", "보험", "보험사", "보험상품명", "보험명", "계약자", "피보험자",
    "계약일", "만기일", "납입", "납입금", "납입액", "납입주기", "납입기간", "월납입액", "총납입금",
    "투자", "투자상품명", "투자금액", "투자원금", "원금", "평가금액", "평가액", "평가손익", "수익률",
    "종목", "종목명", "보유수량", "수량", "대출금액", "대출잔액", "대출상품명", "금리", "이자", "개설일",
    "상환일", "만기", "시작일", "종료일", "기간", "기준일", "기준", "상태", "번호", "종류", "유형", "비고",
    "날짜", "시간", "타입", "대분류", "소분류", "내용", "화폐", "결제수단", "메모",
}
SECTION_RE = re.compile(r"^\s*\d+\s*[.)]\s*[^\d]{1,30}$")
MONTH_RE = re.compile(r"^(\d{4}[-./]\d{1,2}|\d{1,2}월)$")
SAFE_FILENAME_WORDS = {"뱅크샐러드", "가계부", "내역", "내보내기", "데이터", "엑셀"}

# 금액·이름 없이 "어떤 행이 어떻게 표현되는지"만 세기 위한 단어
PROBE_KEYWORDS = [
    "카드대금", "결제대금", "대금", "자동이체", "충전", "페이", "머니", "이자", "원리금", "대출", "상환",
    "적금", "예금", "청약", "증권", "투자", "펀드", "보험", "취소", "환불", "할부", "해외", "캐시백",
    "포인트", "급여", "월급", "더치",
]


class ArchiveError(Exception):
    """사용자에게 그대로 보여줘도 되는 오류. 메시지에 파일 내용을 넣지 않는다."""


def mask_digits(text: str) -> str:
    return re.sub(r"\d", "#", text)


def mask_filename(name: str) -> str:
    """확장자와 숫자·구분 기호만 남긴다. 인코딩이 깨진 이름도 함께 가려진다."""
    stem, dot, extension = name.rpartition(".")
    if not dot:
        stem, extension = name, ""
    masked = re.sub(
        r"[^\W\d_]+",
        lambda m: m.group() if m.group() in SAFE_FILENAME_WORDS else "<글자>",
        unicodedata.normalize("NFC", stem),
    )
    return f"{masked}{dot}{extension}"


def redact_customer_name(text: str, filename: str) -> str:
    """'<이름>님_기간.zip' 형식의 파일 이름에서 이름을 알아내 출력 전체에서 가린다."""
    match = re.match(r"^(.+?)님_", unicodedata.normalize("NFC", filename))
    return text.replace(match.group(1), "<이름>") if match else text


def describe_encryption(info: zipfile.ZipInfo) -> str:
    if not info.flag_bits & 0x1:
        return "암호화 없음"
    if info.flag_bits & 0x40:
        return "비표준 강력 암호화"
    if info.compress_type == WINZIP_AES_METHOD:
        extra = info.extra
        offset = 0
        while offset + 4 <= len(extra):
            header_id, size = struct.unpack("<HH", extra[offset : offset + 4])
            if header_id == WINZIP_AES_EXTRA_ID and size >= 7:
                version, _vendor, strength, method = struct.unpack("<H2sBH", extra[offset + 4 : offset + 11])
                bits = AES_STRENGTH_BITS.get(strength, "?")
                return f"WinZip AES-{bits} (AE-{version}, 내부 압축 방식 {method})"
            offset += 4 + size
        return "WinZip AES (세부 정보 없음)"
    method = {0: "Stored", 8: "Deflate"}.get(info.compress_type, str(info.compress_type))
    return f"ZipCrypto, 압축 방식 {method} [약한 암호화]"


def read_single_member(path: Path, password: str, out: TextIO) -> bytes:
    size = path.stat().st_size
    if size > MAX_ARCHIVE_BYTES:
        raise ArchiveError(f"첨부가 제한({MAX_ARCHIVE_BYTES}바이트)보다 큽니다.")
    print(f"파일 크기: {size} 바이트", file=out)

    try:
        with zipfile.ZipFile(path) as listing:
            infos = [i for i in listing.infolist() if not i.is_dir()]
    except zipfile.BadZipFile as exc:
        raise ArchiveError("ZIP 형식이 아닙니다.") from exc

    print(f"ZIP 안의 파일: {len(infos)}개", file=out)
    total = 0
    for info in infos:
        total += info.file_size
        print(
            f"  - {mask_filename(info.filename)} | 압축 {info.compress_size} 바이트 | "
            f"원본 {info.file_size} 바이트 | {describe_encryption(info)}",
            file=out,
        )
    if total > MAX_UNPACKED_BYTES:
        raise ArchiveError(f"압축 해제 크기가 제한({MAX_UNPACKED_BYTES}바이트)보다 큽니다.")
    if len(infos) != 1:
        raise ArchiveError("ZIP 안의 파일이 1개가 아닙니다. 파일 수를 알려주세요.")

    target = infos[0]
    try:
        with pyzipper.AESZipFile(path) as archive:
            if target.flag_bits & 0x1:
                archive.setpassword(password.encode("utf-8"))
            return archive.read(target.filename)
    except (RuntimeError, zipfile.BadZipFile) as exc:
        raise ArchiveError("ZIP 비밀번호가 맞지 않거나 파일이 손상되었습니다.") from exc


def to_plain_xlsx(data: bytes, password: str, out: TextIO) -> bytes:
    if data.startswith(OLE_MAGIC):
        office = msoffcrypto.OfficeFile(io.BytesIO(data))
        if not office.is_encrypted():
            raise ArchiveError("암호화되지 않은 구형 XLS 형식입니다.")
        print(f"안쪽 파일: 암호화된 엑셀 ({getattr(office, 'type', '형식 미상')})", file=out)
        plain = io.BytesIO()
        try:
            office.load_key(password=password)
            office.decrypt(plain)
        except Exception as exc:  # 라이브러리별 예외 메시지를 내보내지 않는다
            raise ArchiveError("엑셀 비밀번호가 맞지 않습니다.") from exc
        return plain.getvalue()
    if data.startswith(ZIP_MAGIC):
        print("안쪽 파일: 암호 없는 엑셀", file=out)
        return data
    raise ArchiveError(f"알 수 없는 형식입니다. 머리 바이트: {data[:4].hex()}")


def check_xlsx_limits(xlsx: bytes, out: TextIO) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(xlsx)) as package:
            infos = package.infolist()
    except zipfile.BadZipFile as exc:
        raise ArchiveError("엑셀 형식이 아닙니다.") from exc
    if sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
        raise ArchiveError(f"엑셀 내부 크기가 제한({MAX_UNPACKED_BYTES}바이트)보다 큽니다.")
    names = [i.filename for i in infos]
    if "[Content_Types].xml" not in names:
        raise ArchiveError("안쪽 파일이 엑셀이 아니라 일반 ZIP입니다.")
    print(f"엑셀 매크로: {'있음' if any('vbaProject' in n for n in names) else '없음'}", file=out)


def kind_of(value: Any) -> str:
    if value is None or value == "":
        return "빈칸"
    if isinstance(value, bool):
        return "참거짓"
    if isinstance(value, (int, float)):
        return "숫자"
    if isinstance(value, datetime):
        return "일시"
    if isinstance(value, date):
        return "날짜"
    if isinstance(value, time):
        return "시각"
    if isinstance(value, timedelta):
        return "시간차"
    return "텍스트"


def shape_of(text: str) -> str:
    """값은 지우고 모양만 남긴다. 예: 2026-11-04 → 9999-99-99"""
    shaped = re.sub(r"\d", "9", text)
    shaped = re.sub(r"[가-힣]+", "가", shaped)
    return re.sub(r"[A-Za-z]+", "A", shaped)


def sign_of(value: Any) -> str:
    if isinstance(value, str):
        try:
            value = float(value.replace(",", ""))
        except ValueError:
            return "?"
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "?"
    return "+" if value > 0 else "-" if value < 0 else "0"


def month_of(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return f"{value.year:04d}-{value.month:02d}"
    if isinstance(value, str):
        match = re.match(r"^(\d{4})[-./](\d{1,2})", value.strip())
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}"
    return None


def day_of(value: Any) -> str | None:
    if isinstance(value, (datetime, date)):
        return f"{value.year:04d}-{value.month:02d}-{value.day:02d}"
    if isinstance(value, str):
        match = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})", value.strip())
        if match:
            return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"
    return None


def has_seconds(value: Any) -> bool | None:
    if isinstance(value, (datetime, time)):
        return value.second != 0
    if isinstance(value, timedelta):
        return int(value.total_seconds()) % 60 != 0
    if isinstance(value, str):
        match = re.match(r"^\d{1,2}:\d{2}(?::(\d{2}))?", value.strip())
        if match:
            return bool(match.group(1)) and match.group(1) != "00"
    return None


def instrument_kind(name: str) -> str:
    lowered = name.lower()
    if "체크" in lowered:
        return "체크카드"
    if "카드" in lowered:
        return "카드"
    if "페이" in lowered or "pay" in lowered or "머니" in lowered:
        return "페이"
    if any(word in lowered for word in ("통장", "계좌", "입출금", "저축", "은행", "뱅크", "예금", "적금")):
        return "계좌"
    if "현금" in lowered:
        return "현금"
    return "미상"


Row = list[tuple[Any, str | None]]


def read_rows(sheet: Any) -> list[Row]:
    sheet.reset_dimensions()
    rows: list[Row] = []
    for raw in sheet.iter_rows():
        rows.append([(cell.value, getattr(cell, "number_format", None)) for cell in raw])
    width = max((len(r) for r in rows), default=0)
    for row in rows:
        row.extend([(None, None)] * (width - len(row)))
    return rows


def find_header(rows: list[Row]) -> tuple[int, str] | None:
    for index, row in enumerate(rows[:30]):
        texts = {v.strip() for v, _ in row if isinstance(v, str)}
        if len(texts & KNOWN_COLUMNS) >= 3:
            return index, "확정"
    for index, row in enumerate(rows[:30]):
        cells = [v for v, _ in row if v not in (None, "")]
        short_labels = [
            v for v in cells if isinstance(v, str) and len(v.strip()) <= 12 and not re.search(r"\d", v)
        ]
        if len(cells) < 5 or len(short_labels) != len(cells):
            continue
        below = [sum(v not in (None, "") for v, _ in r) for r in rows[index + 1 :] if any(v is not None for v, _ in r)]
        if len(below) >= 5 and sum(n >= len(cells) - 2 for n in below) / len(below) >= 0.8:
            return index, "추정"
    return None


def print_counter(title: str, counter: Counter, out: TextIO, limit: int = 300) -> None:
    print(f"\n[{title}]", file=out)
    if not counter:
        print("  (없음)", file=out)
        return
    for key, count in sorted(counter.items(), key=lambda kv: tuple(str(k) for k in kv[0]) if isinstance(kv[0], tuple) else (str(kv[0]),))[:limit]:
        label = " | ".join(str(k) for k in key) if isinstance(key, tuple) else str(key)
        print(f"  {label}: {count}건", file=out)
    if len(counter) > limit:
        print(f"  ... 외 {len(counter) - limit}개", file=out)


def profile_table(name: str, rows: list[Row], header_index: int, confidence: str, out: TextIO) -> set[str]:
    headers = [
        v.strip() if isinstance(v, str) and v.strip() else f"(이름 없는 열 {get_column_letter(i + 1)})"
        for i, (v, _) in enumerate(rows[header_index])
    ]
    data = [r for r in rows[header_index + 1 :] if any(v not in (None, "") for v, _ in r)]
    column = {header: i for i, header in enumerate(headers)}

    print(f"\n===== 시트 '{name}': 표 형식 (헤더 {header_index + 1}행, {confidence}) =====", file=out)
    print(f"거래 행 수: {len(data)}", file=out)
    print(f"헤더 위쪽 행 수: {header_index}", file=out)
    print(f"열: {', '.join(headers)}", file=out)

    print("\n[열별 형식]", file=out)
    for header, index in column.items():
        values = [r[index][0] for r in data]
        filled = [v for v in values if v not in (None, "")]
        kinds = Counter(kind_of(v) for v in values)
        formats = Counter(r[index][1] for r in data if r[index][0] not in (None, ""))
        line = f"  {header}: 값 {len(filled)}개, 고유값 {len(set(map(str, filled)))}개, 종류 {dict(kinds)}"
        line += f", 셀 서식 {[f for f, _ in formats.most_common(3)]}"
        if header in {"날짜", "시간", "금액"} or "날짜" in header or "시간" in header or "일자" in header:
            shapes = Counter(shape_of(v) for v in filled if isinstance(v, str))
            if shapes:
                line += f", 텍스트 모양 {shapes.most_common(4)}"
        if any(kind_of(v) == "숫자" for v in filled):
            signs = Counter(sign_of(v) for v in filled if kind_of(v) == "숫자")
            decimals = sum(isinstance(v, float) and not v.is_integer() for v in filled)
            line += f", 부호 {dict(signs)}, 소수 {decimals}개"
        print(line, file=out)

    def get(row: Row, header: str) -> Any:
        return row[column[header]][0] if header in column else None

    def text(row: Row, header: str) -> str:
        value = get(row, header)
        return str(value).strip() if value not in (None, "") else "(빈칸)"

    categories: set[str] = set()
    if "대분류" in column or "소분류" in column:
        for row in data:
            categories.update({text(row, "대분류"), text(row, "소분류")})
        categories.discard("(빈칸)")

    if "타입" in column:
        print_counter("타입 × 금액 부호", Counter((text(r, "타입"), sign_of(get(r, "금액"))) for r in data), out)
        print_counter(
            "타입 × 대분류 × 소분류 × 금액 부호",
            Counter((text(r, "타입"), text(r, "대분류"), text(r, "소분류"), sign_of(get(r, "금액"))) for r in data),
            out,
        )

    if "화폐" in column:
        print_counter("화폐", Counter(text(r, "화폐") for r in data), out)

    if "결제수단" in column:
        print("\n[결제수단 (숫자는 #로 가림)]", file=out)
        per_name: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": Counter(), "last": ""})
        for row in data:
            entry = per_name[mask_digits(text(row, "결제수단"))]
            entry["count"][text(row, "타입")] += 1
            day = day_of(get(row, "날짜"))
            if day and day > entry["last"]:
                entry["last"] = day
        for label, entry in sorted(per_name.items()):
            print(
                f"  {label} | 종류 추정 {instrument_kind(label)} | "
                f"{dict(entry['count'])} | 마지막 거래일 {entry['last'] or '미상'}",
                file=out,
            )

        print_counter(
            "결제수단 종류 × 타입 × 금액 부호",
            Counter((instrument_kind(text(r, "결제수단")), text(r, "타입"), sign_of(get(r, "금액"))) for r in data),
            out,
        )

    searchable = [h for h in ("내용", "대분류", "소분류", "메모") if h in column]
    if searchable:
        probes: Counter = Counter()
        for row in data:
            haystack = " ".join(text(row, h) for h in searchable)
            for keyword in PROBE_KEYWORDS:
                if keyword in haystack:
                    probes[(keyword, text(row, "타입"), instrument_kind(text(row, "결제수단")), sign_of(get(row, "금액")))] += 1
        print_counter("단어 포함 건수: 단어 | 타입 | 결제수단 종류 | 금액 부호 (내용은 출력 안 함)", probes, out)

    key_columns = [h for h in ("날짜", "시간", "타입", "내용", "금액", "결제수단") if h in column]
    if key_columns:
        full_key = Counter(tuple(str(get(r, h)) for h in key_columns) for r in data)
        no_time = Counter(tuple(str(get(r, h)) for h in key_columns if h != "시간") for r in data)
        duplicates = [c for c in full_key.values() if c > 1]
        print("\n[중복 판단 재료]", file=out)
        print(f"  키 열: {key_columns}", file=out)
        print(f"  완전히 같은 행 묶음: {len(duplicates)}개, 최대 묶음 크기 {max(duplicates, default=1)}", file=out)
        print(f"  시간을 뺐을 때 같은 행 묶음: {sum(c > 1 for c in no_time.values())}개", file=out)

    print("\n[고유 ID 후보 열 (모든 행의 값이 서로 다른 열)]", file=out)
    candidates = []
    for header, index in column.items():
        values = [r[index][0] for r in data if r[index][0] not in (None, "")]
        if len(data) >= 2 and len(values) == len(data) and len(set(map(str, values))) == len(values):
            candidates.append(header)
    print(f"  {candidates or '없음'}", file=out)

    if "시간" in column:
        seconds = [has_seconds(get(r, "시간")) for r in data]
        known = [s for s in seconds if s is not None]
        print(f"\n[시간 정밀도] 분석 가능 {len(known)}행 중 초 단위 값이 있는 행 {sum(known)}개", file=out)

    if "날짜" in column:
        days = sorted(d for d in (day_of(get(r, "날짜")) for r in data) if d)
        if days:
            print(f"\n[날짜 범위] {days[0]} ~ {days[-1]}", file=out)
        print_counter(
            "월별 × 타입 건수",
            Counter((month_of(get(r, "날짜")) or "(미상)", text(r, "타입")) for r in data),
            out,
        )

    if "메모" in column:
        print(f"\n[메모가 있는 행] {sum(get(r, '메모') not in (None, '') for r in data)}개", file=out)

    return categories


def cell_token(value: Any, column_index: int, allow_all_text: bool, labels: set[str]) -> str:
    letter = get_column_letter(column_index + 1)
    kind = kind_of(value)
    if kind == "텍스트":
        stripped = value.strip()
        if stripped in labels or SECTION_RE.match(stripped) or MONTH_RE.match(stripped):
            return f'{letter}="{stripped}"'
        if allow_all_text and not re.search(r"\d", stripped) and len(stripped) <= 15:
            return f'{letter}="{stripped}"'
        return f"{letter}=<글{len(stripped)}>"
    if kind == "숫자":
        return f"{letter}=<수>"
    if kind in {"일시", "날짜"}:
        return f"{letter}=<날짜 {value.year:04d}-{value.month:02d}>"
    return f"{letter}=<{kind}>"


def print_layout(name: str, rows: list[Row], labels: set[str], out: TextIO) -> None:
    width = len(rows[0]) if rows else 0
    print(f"\n===== 시트 '{name}': 자유 형식 ({len(rows)}행 × {width}열) =====", file=out)
    print('표기: "글자"=구조용 단어, <글N>=가린 텍스트(N글자), <수>=가린 숫자', file=out)

    in_customer_section = False
    previous_signature: tuple | None = None
    run_start = 0
    run_line = ""

    def flush(last_row: int) -> None:
        if previous_signature is None:
            return
        if last_row > run_start:
            print(f"  R{run_start}-R{last_row} ({last_row - run_start + 1}행 같은 구조): {run_line}", file=out)
        else:
            print(f"  R{run_start}: {run_line}", file=out)

    last_row = 0
    for number, row in enumerate(rows, start=1):
        cells = [(i, v) for i, (v, _) in enumerate(row) if v not in (None, "")]
        if not cells:
            continue

        texts = [v.strip() for _, v in cells if isinstance(v, str)]
        title = next((t for t in texts if SECTION_RE.match(t)), None)
        if title:
            in_customer_section = "고객" in title
        elif any(t in {"고객정보"} for t in texts):
            in_customer_section = True

        # 전부 짧은 텍스트이고 구조용 단어를 포함한 행은 표의 헤더로 본다 (고객정보 구역 제외)
        all_text = len(texts) == len(cells) and len(cells) >= 3
        is_header_row = all_text and not in_customer_section and bool(set(texts) & SAFE_LABELS)
        tokens = [cell_token(v, i, is_header_row, labels) for i, v in cells]
        line = " ".join(tokens)
        signature = tuple(re.sub(r"<글\d+>", "<글>", t) for t in tokens)

        if signature == previous_signature and last_row == number - 1:
            last_row = number
            continue
        flush(last_row)
        previous_signature, run_start, run_line, last_row = signature, number, line, number
    flush(last_row)


def inspect_export(path: Path, password: str, out: TextIO) -> None:
    if not DEFUSEDXML:
        raise ArchiveError("defusedxml이 설치되어 있지 않습니다.")
    print("===== ZIP =====", file=out)
    inner = read_single_member(path, password, out)
    xlsx = to_plain_xlsx(inner, password, out)
    check_xlsx_limits(xlsx, out)

    workbook = openpyxl.load_workbook(io.BytesIO(xlsx), read_only=True, data_only=True)
    try:
        sheets = [(name, read_rows(workbook[name])) for name in workbook.sheetnames]
    finally:
        workbook.close()
    print(f"시트: {[name for name, _ in sheets]}", file=out)

    labels = set(SAFE_LABELS)
    layouts = []
    for name, rows in sheets:
        header = None if "현황" in name else find_header(rows)
        if header:
            labels |= profile_table(name, rows, header[0], header[1], out)
        else:
            layouts.append((name, rows))
    for name, rows in layouts:
        print_layout(name, rows, labels, out)

    print("\n===== 끝 =====", file=out)
    print("이 출력에는 금액, 거래 내용, 메모가 없습니다.", file=out)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("사용법: inspect_export.py <ZIP 경로> (ZIP 파일 하나만 지정)", file=sys.stderr)
        return 2
    path = Path(args[0]).expanduser()
    if not path.is_file():
        print("오류: 파일을 찾을 수 없습니다.", file=sys.stderr)
        return 2

    password = getpass.getpass("내보내기 비밀번호 (입력해도 화면에 표시되지 않습니다): ")
    report = io.StringIO()
    try:
        inspect_export(path, password, report)
    except ArchiveError as exc:
        print(redact_customer_name(report.getvalue(), path.name))
        print(f"\n오류: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # 메시지에 셀 값이 섞일 수 있어 종류와 위치만 출력한다
        print(redact_customer_name(report.getvalue(), path.name))
        frame = traceback.extract_tb(exc.__traceback__)[-1]
        print(f"\n오류: 예기치 못한 오류 {type(exc).__name__} ({Path(frame.filename).name}:{frame.lineno})", file=sys.stderr)
        return 1
    print(redact_customer_name(report.getvalue(), path.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())

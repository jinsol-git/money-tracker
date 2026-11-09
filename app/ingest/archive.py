"""내보내기 ZIP을 메모리에서만 열어 엑셀 바이트를 돌려준다.

뱅크샐러드 ZIP은 약한 ZipCrypto 방식이지만, 암호가 맞아야 CRC 검사까지 통과하므로
'저장된 비밀번호로 열리는 파일'만 받는다. 오류 메시지에는 파일 내용을 넣지 않는다.
"""

from __future__ import annotations

import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

import pyzipper

MAX_ARCHIVE_BYTES = 4 * 1024 * 1024
MAX_UNPACKED_BYTES = 20 * 1024 * 1024  # 실제 파일은 100KB 남짓이다. 함수 메모리(2GB)를 지키는 상한이다.
PERIOD_RE = re.compile(r"(\d{4}-\d{2}-\d{2})~(\d{4}-\d{2}-\d{2})")


class ExportRejected(Exception):
    """내보내기 파일이 아니어서 보관하지 않는 경우."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class WrongPassword(Exception):
    """저장된 비밀번호로 열리지 않는 경우. 재입력 후 다시 처리할 수 있어야 한다."""


@dataclass(frozen=True)
class OpenedExport:
    xlsx: bytes
    period: tuple[date, date] | None
    password_index: int


def _period_from(name: str) -> tuple[date, date] | None:
    match = PERIOD_RE.search(name)
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1)), date.fromisoformat(match.group(2))
    except ValueError:
        return None


def _check_workbook_package(xlsx: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(xlsx)) as package:
            infos = package.infolist()
    except Exception as exc:  # 망가진 파일은 어떤 예외든 낼 수 있다. 500이 되면 수신이 재시도를 반복한다.
        raise ExportRejected("not_workbook", "안쪽 파일이 엑셀이 아닙니다.") from exc
    names = {info.filename for info in infos}
    if "[Content_Types].xml" not in names:
        raise ExportRejected("not_workbook", "안쪽 파일이 엑셀이 아닙니다.")
    if sum(info.file_size for info in infos) > MAX_UNPACKED_BYTES:
        raise ExportRejected("too_large", "엑셀의 압축 해제 크기가 제한을 넘습니다.")
    if any("vbaProject" in name for name in names):
        raise ExportRejected("macro", "매크로가 있는 엑셀은 받지 않습니다.")


def open_export(data: bytes, passwords: Sequence[str]) -> OpenedExport:
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ExportRejected("too_large", "첨부 크기가 제한을 넘습니다.")
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as listing:
            members = [info for info in listing.infolist() if not info.is_dir()]
    except Exception as exc:  # 망가진 파일은 어떤 예외든 낼 수 있다
        raise ExportRejected("not_zip", "ZIP 파일이 아닙니다.") from exc

    if len(members) != 1:
        raise ExportRejected("unexpected_members", "ZIP 안의 파일이 1개가 아닙니다.")
    member = members[0]
    if not member.filename.lower().endswith(".xlsx"):
        raise ExportRejected("unexpected_members", "ZIP 안의 파일이 엑셀이 아닙니다.")
    if member.file_size > MAX_UNPACKED_BYTES:
        raise ExportRejected("too_large", "압축 해제 크기가 제한을 넘습니다.")
    if not member.flag_bits & 0x1:
        raise ExportRejected("unencrypted", "암호가 없는 ZIP은 받지 않습니다.")

    for index, password in enumerate(passwords):
        try:
            with pyzipper.AESZipFile(io.BytesIO(data)) as archive:
                xlsx = archive.read(member.filename, pwd=password.encode("utf-8"))
        except Exception:  # noqa: BLE001 - 틀린 암호와 망가진 파일은 라이브러리마다 다른 예외를 낸다
            continue
        _check_workbook_package(xlsx)
        return OpenedExport(xlsx=xlsx, period=_period_from(member.filename), password_index=index)
    raise WrongPassword("저장된 비밀번호로 파일을 열 수 없습니다.")

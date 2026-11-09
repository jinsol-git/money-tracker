"""구조 확인 스크립트가 형식을 올바로 보고하고, 민감한 값을 내보내지 않는지 확인한다."""

from __future__ import annotations

import io
import unicodedata
import zipfile

import pytest

import inspect_export as tool
from fixtures import make_export as fx

PASSWORD = "test-password-123"


def run(tmp_path, data: bytes, password: str = PASSWORD) -> str:
    path = tmp_path / "export.zip"
    path.write_bytes(data)
    buffer = io.StringIO()
    tool.inspect_export(path, password, buffer)
    return buffer.getvalue()


def assert_no_sensitive_values(text: str) -> None:
    for value in fx.SENTINELS:
        assert value not in text, f"민감한 값이 출력됨: {value}"


@pytest.fixture
def xlsx() -> bytes:
    return fx.build_xlsx()


def test_aes_zip_reports_structure_without_values(tmp_path, xlsx):
    output = run(tmp_path, fx.zip_aes(xlsx, PASSWORD))

    assert "WinZip AES-256" in output
    assert "암호 없는 엑셀" in output
    assert "뱅샐현황" in output
    assert "가계부 내역" in output
    assert "거래 행 수: 7" in output
    assert "열: 날짜, 시간, 타입, 대분류, 소분류, 내용, 금액, 화폐, 결제수단, 메모" in output
    assert_no_sensitive_values(output)


def test_table_profile_counts(tmp_path, xlsx):
    output = run(tmp_path, fx.zip_aes(xlsx, PASSWORD))

    assert "지출 | 식비 | 카페 | -: 2건" in output
    assert "지출 | 쇼핑 | 온라인쇼핑 | +: 1건" in output  # 양수 지출(취소로 추정) 행을 구분한다
    assert "이체 | +" not in output
    assert "KRW: 6건" in output
    assert "USD: 1건" in output
    assert "완전히 같은 행 묶음: 1개, 최대 묶음 크기 2" in output
    assert "[메모가 있는 행] 1개" in output
    assert "[날짜 범위] 2026-01-03 ~ 2026-02-13" in output
    assert "2026-02 | 이체: 2건" in output
    assert "초 단위 값이 있는 행 2개" in output
    assert "카드대금 | 이체 | 계좌 | -: 1건" in output


def test_instrument_digits_are_masked(tmp_path, xlsx):
    output = run(tmp_path, fx.zip_aes(xlsx, PASSWORD))

    assert "토스뱅크 ###-###-######" in output
    assert "신한카드 ####-####" in output
    assert "종류 추정 카드" in output
    assert "종류 추정 계좌" in output
    assert "종류 추정 페이" in output


def test_free_form_sheet_shows_labels_only(tmp_path, xlsx):
    output = run(tmp_path, fx.zip_aes(xlsx, PASSWORD))

    assert '"1.고객정보"' in output
    assert '"이름"' in output
    assert '"3.재무현황"' in output
    assert '"상품명"' in output
    assert '"2026-01"' in output
    assert '"식비"' in output  # 내역 시트에서 나온 분류 이름은 구조로 본다
    assert "<수>" in output
    assert f"<글{len(fx.CUSTOMER_NAME)}>" in output


@pytest.mark.skipif(not fx.shutil.which("zip"), reason="zip 명령 없음")
def test_zipcrypto_is_flagged_as_weak(tmp_path, xlsx):
    output = run(tmp_path, fx.zip_crypto(xlsx, PASSWORD, tmp_path))

    assert "ZipCrypto" in output
    assert "약한 암호화" in output
    assert "거래 행 수: 7" in output
    assert_no_sensitive_values(output)


def test_encrypted_xlsx_in_plain_zip(tmp_path, xlsx):
    output = run(tmp_path, fx.zip_plain(fx.encrypt_xlsx(xlsx, PASSWORD)))

    assert "암호화 없음" in output
    assert "암호화된 엑셀" in output
    assert "거래 행 수: 7" in output
    assert_no_sensitive_values(output)


def test_wrong_zip_password_is_reported_without_values(tmp_path, xlsx):
    with pytest.raises(tool.ArchiveError, match="ZIP 비밀번호"):
        run(tmp_path, fx.zip_aes(xlsx, PASSWORD), password="wrong")


def test_wrong_xlsx_password_is_reported(tmp_path, xlsx):
    with pytest.raises(tool.ArchiveError, match="엑셀 비밀번호"):
        run(tmp_path, fx.zip_plain(fx.encrypt_xlsx(xlsx, PASSWORD)), password="wrong")


def test_oversized_archive_is_rejected(tmp_path):
    with pytest.raises(tool.ArchiveError, match="첨부가 제한"):
        run(tmp_path, b"PK" + b"\0" * (tool.MAX_ARCHIVE_BYTES + 1))


def test_zip_bomb_is_rejected_before_reading(tmp_path):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        with archive.open("bomb.xlsx", "w", force_zip64=True) as member:
            chunk = b"\0" * (1024 * 1024)
            for _ in range(tool.MAX_UNPACKED_BYTES // len(chunk) + 1):
                member.write(chunk)
    with pytest.raises(tool.ArchiveError, match="압축 해제 크기"):
        run(tmp_path, buffer.getvalue())


def test_nested_zip_is_not_treated_as_workbook(tmp_path):
    inner = fx.zip_plain(b"not a workbook", inner_name="nested.bin")
    with pytest.raises(tool.ArchiveError, match="일반 ZIP"):
        run(tmp_path, fx.zip_plain(inner))


def test_filename_hides_person_name():
    assert tool.mask_filename("김철수님_2026-01-01~2026-11-04.xlsx") == "<글자>_2026-01-01~2026-11-04.xlsx"
    assert tool.mask_filename("뱅크샐러드_내역.xlsx") == "뱅크샐러드_내역.xlsx"
    assert tool.mask_filename("kimcs_2026.xlsx") == "<글자>_2026.xlsx"
    # CP949 이름이 CP437로 잘못 풀린 경우에도 원문이 남지 않는다
    garbled = "김철수님".encode("cp949").decode("cp437")
    assert garbled not in tool.mask_filename(f"{garbled}_2026.xlsx")


def test_customer_name_from_filename_is_redacted_everywhere():
    decomposed = unicodedata.normalize("NFD", "김철수님_2026-01-01~2026-11-04.zip")  # macOS 파일 이름 형식
    assert tool.redact_customer_name("김철수 통장 | 김철수", decomposed) == "<이름> 통장 | <이름>"
    assert tool.redact_customer_name("김철수 통장", "renamed.zip") == "김철수 통장"


def test_main_redacts_customer_name_from_output(tmp_path, monkeypatch, capsys, xlsx):
    path = tmp_path / f"{fx.CUSTOMER_NAME}님_2026-01-01~2026-11-04.zip"
    path.write_bytes(fx.zip_aes(xlsx, PASSWORD))
    monkeypatch.setattr(tool.getpass, "getpass", lambda _prompt: PASSWORD)
    original = tool.inspect_export

    def leaky(zip_path, password, out):
        original(zip_path, password, out)
        out.write(f"결제수단에 들어간 이름: {fx.CUSTOMER_NAME}\n")

    monkeypatch.setattr(tool, "inspect_export", leaky)
    assert tool.main([str(path)]) == 0
    captured = capsys.readouterr().out
    assert fx.CUSTOMER_NAME not in captured
    assert "결제수단에 들어간 이름: <이름>" in captured
    assert_no_sensitive_values(captured)


def test_main_hides_unexpected_error_messages(tmp_path, monkeypatch, capsys):
    path = tmp_path / "export.zip"
    path.write_bytes(b"placeholder")
    monkeypatch.setattr(tool.getpass, "getpass", lambda _prompt: PASSWORD)

    def explode(*_args):
        raise ValueError(fx.MERCHANT)

    monkeypatch.setattr(tool, "inspect_export", explode)
    assert tool.main([str(path)]) == 1
    captured = capsys.readouterr()
    assert "ValueError" in captured.err
    assert fx.MERCHANT not in captured.err + captured.out


def test_main_requires_exactly_one_file(capsys):
    assert tool.main(["a.zip", "b.zip"]) == 2
    assert "ZIP 파일 하나만" in capsys.readouterr().err

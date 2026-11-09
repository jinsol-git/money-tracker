"""ZIP 열기, 파싱, 합계 대조."""

from __future__ import annotations

import dataclasses
import io
import zipfile
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.ingest import archive as archive_module
from app.ingest import parser as parser_module
from app.ingest.archive import ExportRejected, WrongPassword, open_export
from app.ingest.parser import FormatChanged, mask_numbers, parse_workbook
from app.ingest.reconcile import ReconciliationFailed, verify_summary
from fixtures import make_export as fx

PASSWORD = "0000"


@pytest.fixture
def xlsx() -> bytes:
    return fx.build_xlsx()


def test_open_aes_zip_and_read_period(xlsx):
    opened = open_export(fx.zip_aes(xlsx, PASSWORD), [PASSWORD])
    assert opened.xlsx == xlsx
    assert opened.period == (date(2026, 1, 1), date(2026, 2, 28))
    assert opened.password_index == 0


@pytest.mark.skipif(not fx.shutil.which("zip"), reason="zip 명령 없음")
def test_open_zipcrypto_like_the_real_file(xlsx, tmp_path):
    opened = open_export(fx.zip_crypto(xlsx, PASSWORD, tmp_path), [PASSWORD])
    assert opened.xlsx == xlsx


def test_previous_password_is_tried_in_order(xlsx):
    opened = open_export(fx.zip_aes(xlsx, PASSWORD), ["1111", PASSWORD])
    assert opened.password_index == 1


def test_wrong_password_is_distinguishable(xlsx):
    with pytest.raises(WrongPassword):
        open_export(fx.zip_aes(xlsx, PASSWORD), ["1111", "2222"])


def test_unencrypted_zip_is_rejected(xlsx):
    with pytest.raises(ExportRejected) as error:
        open_export(fx.zip_plain(xlsx), [PASSWORD])
    assert error.value.code == "unencrypted"


def test_oversized_and_non_zip_are_rejected():
    with pytest.raises(ExportRejected) as error:
        open_export(b"\0" * (archive_module.MAX_ARCHIVE_BYTES + 1), [PASSWORD])
    assert error.value.code == "too_large"
    with pytest.raises(ExportRejected) as error:
        open_export(b"not a zip", [PASSWORD])
    assert error.value.code == "not_zip"


def test_zip_bomb_is_rejected_before_decompressing():
    bomb = fx.zip_aes(b"\0" * (archive_module.MAX_UNPACKED_BYTES + 1), PASSWORD)
    with pytest.raises(ExportRejected) as error:
        open_export(bomb, [PASSWORD])
    assert error.value.code == "too_large"


def test_non_workbook_inside_zip_is_rejected():
    with pytest.raises(ExportRejected) as error:
        open_export(fx.zip_aes(b"plain text", PASSWORD), [PASSWORD])
    assert error.value.code == "not_workbook"


def test_corrupt_encrypted_zip_is_wrong_password_not_a_crash(xlsx):
    data = bytearray(fx.zip_aes(xlsx, PASSWORD))
    middle = len(data) // 2
    data[middle:middle + 40] = b"\xff" * 40
    with pytest.raises((WrongPassword, ExportRejected)):
        open_export(bytes(data), [PASSWORD])


def test_arbitrary_bytes_never_raise_unexpected_errors():
    for blob in (b"", b"PK", b"PK\x03\x04" + b"\x00" * 200, b"PK\x05\x06" + b"\x00" * 18):
        with pytest.raises((WrongPassword, ExportRejected)):
            open_export(blob, [PASSWORD])


def test_broken_sheet_xml_is_a_format_change():
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(fx.build_xlsx())) as source, zipfile.ZipFile(buffer, "w") as target:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename.endswith("sheet2.xml"):
                content = content[: len(content) // 2]
            target.writestr(item, content)
    with pytest.raises(FormatChanged):
        parse_workbook(buffer.getvalue())


def test_too_many_rows_are_rejected(monkeypatch, xlsx):
    monkeypatch.setattr(parser_module, "MAX_ROWS", 3)
    with pytest.raises(FormatChanged, match="상한"):
        parse_workbook(xlsx)


def test_parse_rows_with_types_and_seconds(xlsx):
    parsed = parse_workbook(xlsx)
    assert len(parsed.rows) == len(fx.ROWS)
    first = next(row for row in parsed.rows if row.type == "수입")
    assert first.occurred_at == datetime(2026, 1, 25, 0, 0, 0)
    assert first.amount == Decimal(fx.INCOME_AMOUNT)
    cafe = next(row for row in parsed.rows if row.memo)
    assert cafe.occurred_at.second == 15
    assert cafe.memo == fx.MEMO


def test_long_numbers_are_masked_on_parse(xlsx):
    parsed = parse_workbook(xlsx)
    names = {row.instrument for row in parsed.rows}
    assert not any(fx.ACCOUNT_NUMBER in name for name in names)
    assert "토스뱅크 ****6789" in names
    assert "신한카드 ****5678" in names
    assert mask_numbers("CU 강남123점") == "CU 강남123점"
    assert mask_numbers("010-1234-5678 입금") == "****5678 입금"


def test_customer_section_is_not_read(xlsx):
    parsed = parse_workbook(xlsx)
    text = repr(dataclasses.asdict(parsed))
    assert fx.CUSTOMER_NAME not in text
    assert fx.CUSTOMER_EMAIL not in text
    assert "777" not in text  # 신용점수


def test_summary_tables_and_assets(xlsx):
    parsed = parse_workbook(xlsx)
    assert parsed.months == ["2026-01", "2026-02"]
    assert parsed.income_table["급여"]["2026-01"] == fx.INCOME_AMOUNT
    assert parsed.expense_table["식비"]["2026-01"] == fx.SECRET_AMOUNT * 2

    assets = {line.product: line for line in parsed.assets if line.side == "asset"}
    assert assets["토스뱅크 ****6789"].group == "자유입출금 자산"
    assert assets["토스뱅크 ****6789"].amount == fx.NET_WORTH
    assert fx.PRODUCT_NAME in assets
    assert "연금 자산" not in assets  # 상품 없는 항목 행은 건너뛴다
    debts = [line for line in parsed.assets if line.side == "liability"]
    assert [(d.group, d.amount) for d in debts] == [("장기대출", 100)]

    sections = {detail.section for detail in parsed.details}
    assert sections == {"insurance", "investment", "loan"}
    loan = next(detail for detail in parsed.details if detail.section == "loan")
    assert loan.fields["대출잔액"] == 100
    assert loan.fields["대출신규일"] == "2026-02-01"


def test_formula_total_cells_are_ignored(xlsx):
    # 실제 파일에서 합계 칸은 수식이고 값이 없다. 합계 행이 대분류로 들어가면 안 된다.
    parsed = parse_workbook(xlsx)
    labels = set(parsed.income_table) | set(parsed.expense_table)
    assert not labels & {"월수입 총계", "월지출 총계", "순수입 총계"}


@pytest.mark.parametrize(
    "tamper",
    [
        lambda wb: wb.remove(wb["뱅샐현황"]),
        lambda wb: wb["가계부 내역"].__setitem__("G1", "금액(원)"),
        lambda wb: wb["가계부 내역"].__setitem__("C2", "대출"),
        lambda wb: wb["가계부 내역"].__setitem__("G2", "일만원"),
        lambda wb: wb["가계부 내역"].__setitem__("A2", "어제"),
        lambda wb: wb["뱅샐현황"].__setitem__("B9", "2.현금흐름"),
    ],
    ids=["시트 없음", "열 이름 변경", "모르는 타입", "금액이 글자", "날짜가 글자", "구역 제목 변경"],
)
def test_format_change_stops_parsing(tamper):
    with pytest.raises(FormatChanged):
        parse_workbook(fx.build_xlsx(tamper=tamper))


def test_format_error_messages_do_not_contain_values():
    def tamper(workbook):
        workbook["가계부 내역"]["G2"] = fx.MERCHANT

    with pytest.raises(FormatChanged) as error:
        parse_workbook(fx.build_xlsx(tamper=tamper))
    assert fx.MERCHANT not in str(error.value)


def test_decimal_krw_amount_is_a_format_change():
    def tamper(workbook):
        workbook["가계부 내역"]["G2"] = -100.5

    with pytest.raises(FormatChanged):
        parse_workbook(fx.build_xlsx(rows=_krw_rows(), tamper=tamper))


def _krw_rows():
    return [
        fx.tx("2026-01-10 10:00:00", "지출", "식비", "한식", "밥집", -10000, "신한카드"),
        fx.tx("2026-01-11 10:00:00", "지출", "식비", "한식", "밥집", 3000, "신한카드"),
        fx.tx("2026-02-01 09:00:00", "수입", "급여", "미분류", "급여", 500000, "통장"),
        fx.tx("2026-02-02 09:00:00", "이체", "내계좌이체", "미분류", "이동", -7777, "통장"),
    ]


def test_summary_matches_absolute_net_of_refunds():
    parsed = parse_workbook(fx.build_xlsx(rows=_krw_rows()))
    assert parsed.expense_table["식비"]["2026-01"] == 7000  # 취소 차감 후 값
    check = verify_summary(parsed)
    assert check.cells_checked == 4
    assert check.cells_skipped == 0


def test_tampered_summary_cell_is_detected():
    def tamper(workbook):
        sheet = workbook["뱅샐현황"]
        for row in sheet.iter_rows():
            if row[1].value == "식비":
                row[4].value = 1  # 2026-01 칸

    parsed = parse_workbook(fx.build_xlsx(rows=_krw_rows(), tamper=tamper))
    with pytest.raises(ReconciliationFailed) as error:
        verify_summary(parsed)
    assert ("지출", "식비", "2026-01") in error.value.mismatches


def test_ledger_row_missing_from_summary_is_detected():
    rows = _krw_rows()
    xlsx = fx.build_xlsx(rows=rows)
    parsed = parse_workbook(xlsx)
    spend = next(row for row in parsed.rows if row.type == "지출")
    extra = dataclasses.replace(spend, major="새분류", amount=Decimal(-5))
    with pytest.raises(ReconciliationFailed):
        verify_summary(dataclasses.replace(parsed, rows=[*parsed.rows, extra]))


def test_foreign_currency_cells_are_skipped_not_failed():
    rows = [
        fx.tx("2026-01-10 10:00:00", "지출", "문화", "구독", "서비스", -11.5, "페이", currency="USD"),
        fx.tx("2026-01-10 11:00:00", "지출", "식비", "한식", "밥집", -1000, "카드"),
    ]
    parsed = parse_workbook(fx.build_xlsx(rows=rows))
    check = verify_summary(parsed)
    assert check.cells_skipped == 1
    assert check.cells_checked == 1

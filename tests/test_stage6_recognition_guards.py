# -*- coding: utf-8 -*-
"""
Tests for recognition guards (S-10..S-13) — закрытие слепых зон распознавания:
- S-10: дробные доли удержания («1/4 заработка») участвуют в проверке 229-ФЗ.
- S-11: слэш-даты (21/04/2016) парсятся; мусорные даты дают UNPARSEABLE_DATE.
- S-12: кросс-чеки УИН↔РОСП, ОКТМО, КПП, номеров бланков ИЛ, форматов номеров дел.
- S-13: OCR-транскрипция сканов для кросс-модального гейта + штраф галлюцинаций.
"""

import datetime
from unittest.mock import MagicMock


from scan_reader.verifier.math_verifier import parse_percentage_value, verify_deduction_percentage
from scan_reader.verifier.chronology import parse_flexible_date
from scan_reader.verifier.auditor import ZeroTrustAuditor
from scan_reader.verifier.status import VerificationIssue, VerificationReport, VerificationStatus
from scan_reader.core.metrics_evaluator import validate_case_number_format


# ---------------------------------------------------------------------------
# S-10: дробные доли удержания
# ---------------------------------------------------------------------------
def test_s10_fraction_percentages_parsed():
    """S-10: «1/4 заработка» -> 25%, «2/3 дохода» -> 66.67, «1/2» -> 50."""
    assert parse_percentage_value("1/4 заработка") == 25.0
    assert parse_percentage_value("1/2") == 50.0
    assert parse_percentage_value("2/3 дохода") == 66.67
    assert parse_percentage_value("1/3") == 33.33


def test_s10_ip_number_not_misparsed_as_fraction():
    """S-10: номер ИП «10701/16/3001-ИП» не интерпретируется как дробь."""
    assert parse_percentage_value("10701/16/3001-ИП") is None
    assert parse_percentage_value("64947/18/23023-ИП") is None


def test_s10_alimony_fraction_enforces_229fz():
    """S-10: «2/3» (66.67%) без алиментов — нарушение лимита; с алиментами — легально."""
    ok, msg = verify_deduction_percentage("2/3 заработка", has_alimony_or_harm=False)
    assert ok is False  # 66.67% > 50 без алиментов

    ok, msg = verify_deduction_percentage("2/3 заработка", has_alimony_or_harm=True)
    assert ok is True  # алименты: до 70%


def test_s10_quarter_alimony_legal():
    """S-10: «1/4» (алименты) — в пределах лимита."""
    ok, _ = verify_deduction_percentage("1/4 заработка", has_alimony_or_harm=True)
    assert ok is True


# ---------------------------------------------------------------------------
# S-11: слэш-даты и мусорные даты
# ---------------------------------------------------------------------------
def test_s11_slash_dates_parsed():
    """S-11: «21/04/2016» парсится как 2016-04-21."""
    assert parse_flexible_date("21/04/2016") == datetime.date(2016, 4, 21)
    assert parse_flexible_date("01/02/2015") == datetime.date(2015, 2, 1)


def test_s11_invalid_slash_dates_rejected():
    """S-11: невалидные месяцы и номера ИП не превращаются в даты."""
    assert parse_flexible_date("21/13/2016") is None  # месяц 13
    assert parse_flexible_date("10701/16/3001-ИП") is None  # номер ИП


def test_s11_unparseable_date_warns():
    """S-11: мусорная дата в поле -> предупреждение UNPARSEABLE_DATE, не молчание."""
    report = ZeroTrustAuditor.audit_document(
        {"doc_date": "неизвестная дата", "debtor": {"name": "Иванов И.И."}},
        doc_type="salary_deductions",
    )
    codes = [i.code for i in report.issues]
    assert "UNPARSEABLE_DATE" in codes
    assert report.details.get("unparseable_dates") == ["doc_date"]


def test_s11_valid_dates_no_warning():
    """S-11: корректные даты (все форматы) не вызывают предупреждений."""
    report = ZeroTrustAuditor.audit_document(
        {"doc_date": "21.04.2016", "base_doc_date": "20/01/2016", "debtor": {"name": "Иванов И.И."}},
        doc_type="salary_deductions",
    )
    codes = [i.code for i in report.issues]
    assert "UNPARSEABLE_DATE" not in codes


# ---------------------------------------------------------------------------
# S-12: кросс-чеки реквизитов
# ---------------------------------------------------------------------------
def test_s12_uin_rosp_mismatch_warns():
    """S-12: УИН без кода РОСП -> предупреждение UIN_ROSP_MISMATCH."""
    report = ZeroTrustAuditor.audit_document(
        {"payment_details": {"uin": "32299999999999999999", "rosp_code": "10701"}},
        doc_type="salary_deductions",
    )
    codes = [i.code for i in report.issues]
    assert "UIN_ROSP_MISMATCH" in codes


def test_s12_uin_rosp_consistent_no_warning():
    """S-12: УИН с кодом РОСП (кейс пользователя) — без предупреждения."""
    report = ZeroTrustAuditor.audit_document(
        {"payment_details": {"uin": "32230001160010701004", "rosp_code": "10701"}},
        doc_type="salary_deductions",
    )
    codes = [i.code for i in report.issues]
    assert "UIN_ROSP_MISMATCH" not in codes


def test_s12_oktmo_and_kpp_length_checks():
    """S-12: ОКТМО != 8/11 цифр и КПП != 9 цифр -> предупреждения."""
    report = ZeroTrustAuditor.audit_document(
        {"payment_details": {"oktmo": "12345", "recipient_kpp": "123"}},
        doc_type="salary_deductions",
    )
    codes = [i.code for i in report.issues]
    assert "INVALID_OKTMO" in codes
    assert "INVALID_KPP" in codes

    ok_report = ZeroTrustAuditor.audit_document(
        {"payment_details": {"oktmo": "65756000", "recipient_kpp": "773601001"}},
        doc_type="salary_deductions",
    )
    ok_codes = [i.code for i in ok_report.issues]
    assert "INVALID_OKTMO" not in ok_codes
    assert "INVALID_KPP" not in ok_codes


def test_s12_blank_number_length_check():
    """S-12: номер бланка ИЛ с нетипичной длиной -> предупреждение; корректный и короткий — нет."""
    report = ZeroTrustAuditor.audit_document(
        {"blank_number": "0110811", "base_doc_number": "01234567"},  # 7 и 8 цифр
        doc_type="executive_documents",
    )
    codes = [i.code for i in report.issues]
    assert "INVALID_DOC_REF_NUMBER" in codes  # 7 цифр — нетипично

    ok_report = ZeroTrustAuditor.audit_document(
        {"blank_number": "011081166", "base_doc_number": "2-967"},  # 9 цифр и короткий номер
        doc_type="executive_documents",
    )
    ok_codes = [i.code for i in ok_report.issues]
    assert "INVALID_DOC_REF_NUMBER" not in ok_codes


def test_s12_case_number_without_separator_scored_down():
    """S-12: номер дела «12345678» без разделителей — 60 баллов (подозрение склейки)."""
    _, score_bad = validate_case_number_format("12345678")
    _, score_ok = validate_case_number_format("2-1234/2015")
    _, score_ok2 = validate_case_number_format("А40-12345/2021")
    assert score_bad == 60.0
    assert score_ok == 100.0
    assert score_ok2 == 100.0


# ---------------------------------------------------------------------------
# S-13: OCR-транскрипция и штраф галлюцинаций
# ---------------------------------------------------------------------------
def _zt_with_hallucinations(n: int):
    details = {"hallucination_discrepancies": [{"field": f"f{i}"} for i in range(n)]}
    return VerificationReport(
        status=VerificationStatus.VLM_UNVERIFIED,
        is_valid=True,
        issues=[VerificationIssue("warning", "HALLUCINATION_RISK", "x") for _ in range(n)],
        details=details,
    )


def test_s13_hallucination_penalty_applied():
    """S-13: каждое неподтвержденное расхождение -5 п.п. (максимум 15)."""
    from scan_reader.facade import LegalDocPlatformFacade

    validation = {"passed": True, "score": 100.0, "issues": []}
    v, score, status = LegalDocPlatformFacade._combine_quality_and_validation(
        validation, 95.54, "excellent", _zt_with_hallucinations(2)
    )
    assert score == 85.54
    assert status == "high"
    assert any(i["field"] == "cross_modal" for i in v["issues"])


def test_s13_hallucination_penalty_capped():
    """S-13: штраф ограничен 15 п.п."""
    from scan_reader.facade import LegalDocPlatformFacade

    validation = {"passed": True, "score": 100.0, "issues": []}
    v, score, status = LegalDocPlatformFacade._combine_quality_and_validation(
        validation, 95.54, "excellent", _zt_with_hallucinations(10)
    )
    assert score == 80.54


def test_s13_transcribe_scan_for_audit(monkeypatch):
    """S-13: второй VLM-проход возвращает транскрипцию и учитывается в трекере."""
    from scan_reader.facade import LegalDocPlatformFacade

    facade = LegalDocPlatformFacade(enable_cache=False)

    fake_response = MagicMock()
    fake_response.choices = [MagicMock()]
    fake_response.choices[0].message.content = " " + "Постановление об обращении взыскания... " * 5
    fake_response.usage = None

    fake_client = MagicMock()
    fake_client.chat.completions.create.return_value = fake_response
    facade._client = fake_client

    fake_processor = MagicMock()
    fake_processor.prepare_document_inputs.return_value = ("vision", ["data:image/jpeg;base64,xxx"])
    facade.processor = fake_processor

    text = facade._transcribe_scan_for_audit("scan.jpg")
    assert text is not None
    assert "Постановление" in text
    fake_client.chat.completions.create.assert_called_once()
    # В сообщении присутствует изображение
    call = fake_client.chat.completions.create.call_args
    user_content = call.kwargs["messages"][1]["content"]
    assert any(part["type"] == "image_url" for part in user_content)


def test_s13_transcribe_disabled_by_env(monkeypatch):
    """S-13: SCANREADER_OCR_CROSSCHECK=false отключает второй проход."""
    from scan_reader.facade import LegalDocPlatformFacade

    monkeypatch.setenv("SCANREADER_OCR_CROSSCHECK", "false")
    facade = LegalDocPlatformFacade(enable_cache=False)
    facade._client = MagicMock()
    assert facade._transcribe_scan_for_audit("scan.jpg") is None
    facade._client.chat.completions.create.assert_not_called()


def test_s13_gate_catches_digit_mismatch_against_transcription():
    """S-13 (интеграция гейта): искаженный номер ИП против точной транскрипции -> предупреждение."""
    report = ZeroTrustAuditor.audit_document(
        data={"ip_number": "10701/16/30001-ИП", "debtor": {"name": "Оленяк Марина Георгиевна"}},
        doc_type="salary_deductions",
        raw_ocr_text="Материалы ИП №10701/16/3001-ИП. Должник: Оленяк Марина Георгиевна.",
    )
    codes = [i.code for i in report.issues]
    assert "HALLUCINATION_RISK" in codes
    hall_fields = [i.field_name for i in report.issues if i.code == "HALLUCINATION_RISK"]
    assert "ip_number" in hall_fields
    assert "debtor.name" not in hall_fields  # ФИО подтверждено

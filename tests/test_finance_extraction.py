# -*- coding: utf-8 -*-
"""
Тесты модуля парсинга и извлечения денежных сумм (core/finance_parser.py)
и валидаторов финансовых схем Pydantic.
"""

from scan_reader.core.finance_parser import parse_russian_currency, extract_amounts_from_text
from scan_reader.doc_types.salary_deductions.schema import SalaryDeductionDoc
from scan_reader.doc_types.executive_documents.schema import ExecutiveDocumentDoc


def test_parse_russian_currency_variations():
    # Числа
    assert parse_russian_currency(100) == 100.0
    assert parse_russian_currency(12345.67) == 12345.67

    # Простые строки с валютой
    assert parse_russian_currency("100 000 руб.") == 100000.0
    assert parse_russian_currency("45000,00 ₽") == 45000.0
    assert parse_russian_currency("125.432,50 руб") == 125432.50
    assert parse_russian_currency("125 432.50") == 125432.50

    # Пропись в скобках + копейки
    assert parse_russian_currency("100 000 (сто тысяч) руб. 50 коп.") == 100000.50
    assert parse_russian_currency("5000 руб 25 копеек") == 5000.25

    # Пустые и нулевые значения
    assert parse_russian_currency(None) is None
    assert parse_russian_currency("-") is None
    assert parse_russian_currency("нет") is None
    assert parse_russian_currency("0") == 0.0
    assert parse_russian_currency("0.00 руб") == 0.0


def test_extract_amounts_from_judicial_text():
    sample_text = (
        "Суд постановил: взыскать с ООО 'Должник' в пользу ПАО 'Сбербанк' "
        "основной долг в размере 250 000 руб. 00 коп., проценты в сумме 15 000 руб., "
        "а также расходы по оплате госпошлины 6 000 руб. Итого к взысканию: 271 000 руб."
    )
    res = extract_amounts_from_text(sample_text)
    assert res["main_debt_rub"] == 250000.0
    assert res["penalty_rub"] == 15000.0
    assert res["court_fee_rub"] == 6000.0
    assert res["total_rub"] == 271000.0


def test_extract_amounts_from_salary_order_text():
    sample_text = (
        "Постановление судебного пристава: обратить взыскание на заработную плату "
        "должника в размере 50% ежемесячно до погашения общей суммы задолженности в размере 84 500 руб. 50 коп."
    )
    res = extract_amounts_from_text(sample_text)
    assert res["total_rub"] == 84500.50


def test_pydantic_schema_coercion_from_raw_strings():
    # Проверка автоматической очистки строк в схемах Pydantic
    raw_salary = {
        "doc_type": "Постановление об обращении взыскания на заработную плату",
        "finances": {
            "debt_amount_rub": "45 000 (сорок пять тысяч) руб. 00 коп.",
            "total_deduction_rub": "45.000,00 ₽",
            "fee_penalty_rub": "3 150 руб."
        }
    }
    doc = SalaryDeductionDoc.model_validate(raw_salary)
    assert doc.finances.debt_amount_rub == 45000.0
    assert doc.finances.total_deduction_rub == 45000.0
    assert doc.finances.fee_penalty_rub == 3150.0

    raw_exec = {
        "doc_type": "Исполнительный лист",
        "finances": {
            "main_debt_rub": "1 500 000 руб.",
            "interest_penalty_rub": "120 000 руб. 50 коп.",
            "court_fee_rub": "60 000 руб.",
            "total_rub": "1 680 000,50 руб."
        }
    }
    doc_exec = ExecutiveDocumentDoc.model_validate(raw_exec)
    assert doc_exec.finances.main_debt_rub == 1500000.0
    assert doc_exec.finances.interest_penalty_rub == 120000.50
    assert doc_exec.finances.court_fee_rub == 60000.0
    assert doc_exec.finances.total_rub == 1680000.50


def test_pydantic_schema_coercion_from_nested_structures():
    """Проверка устойчивости Pydantic-схем к вложенным dict и list от LLM/VLM."""
    raw_salary_from_vlm = {
        "base_doc": {"type": "Исполнительный лист", "date": "20.01.2016"},
        "claimant_details": {"address": "ул. Примерная, 12", "bik": "041203001"},
        "debtor_details": {"birth_date": "13.05.1980", "address": "Россия, 414004"},
        "legal_base": ["ст. 6, 7.14, 68, 98", "99 ФЗ № 229-ФЗ"],
        "finances": {
            "total_deduction_rub": "45000",
            "deduction_percentage": 50
        }
    }
    doc = SalaryDeductionDoc.model_validate(raw_salary_from_vlm)
    assert "Исполнительный лист" in doc.base_doc
    assert "041203001" in doc.claimant_details
    assert "13.05.1980" in doc.debtor_details
    assert "229-ФЗ" in doc.legal_base
    assert doc.finances.total_deduction_rub == 45000.0
    assert doc.finances.deduction_percentage == "50"


def test_universal_document_schema_validation():
    """Проверка универсальной схемы для неопределенных документов."""
    from scan_reader.facade import UniversalDocumentDoc
    data = {
        "court_or_authority": {"name": "Мировой суд", "court_type": "Мировой"},
        "debtor_name": "Иванов И.И.",
        "total_rub": "12 500,50 руб."
    }
    doc = UniversalDocumentDoc.model_validate(data)
    assert "Мировой суд" in doc.court_or_authority
    assert doc.debtor_name == "Иванов И.И."
    assert doc.total_rub == 12500.50


def test_payment_details_and_quality_flags_schema():
    """Проверка синхронизированных полей payment_details и флагов качества."""
    from scan_reader.type_registry import get_plugin

    # 1. Salary deductions
    salary_plugin = get_plugin("salary_deductions")
    assert salary_plugin is not None
    salary_doc = salary_plugin.schema_cls(
        payment_details={
            "bik": "015004950",
            "payment_account": "03212643000000015113",
            "recipient": "УФК по Приморскому краю",
            "uin": "32225013200000000001",
            "rosp_code": "25013"
        },
        has_signature_stamp=True,
        handwritten_elements=False,
        is_anonymized=False
    )
    assert salary_doc.payment_details.bik == "015004950"
    assert salary_doc.payment_details.payment_account == "03212643000000015113"
    assert salary_doc.payment_details.rosp_code == "25013"
    assert salary_doc.has_signature_stamp is True
    assert "payment_details" in salary_plugin.prompt_text
    assert "payment_account" in salary_plugin.prompt_text

    # 2. Executive documents
    exec_plugin = get_plugin("executive_documents")
    assert exec_plugin is not None
    exec_doc = exec_plugin.schema_cls(
        case_number="А40-12345/2021",
        has_signature_stamp=True,
        is_anonymized=False
    )
    assert exec_doc.has_signature_stamp is True
    assert "has_signature_stamp" in exec_plugin.prompt_text

    # 3. Enforcement orders
    enf_plugin = get_plugin("enforcement_orders")
    assert enf_plugin is not None
    enf_doc = enf_plugin.schema_cls(
        ip_number="12345/21/77001-ИП",
        is_anonymized=True,
        handwritten_elements=False
    )
    assert enf_doc.is_anonymized is True
    assert "is_anonymized" in enf_plugin.prompt_text




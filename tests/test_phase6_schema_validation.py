# -*- coding: utf-8 -*-
"""
Тесты Шазы 6.2 — реальная валидация полей в Pydantic-схемах.

До этого шага во всех девяти схемах было 43 нормализатора (mode="before") и НОЛЬ
ограничений: pattern=0, ge=0, le=0, Literal=0, @model_validator=0. Схема
принимала 15-значный «ИНН», deduction_percentage="999%" и отрицательную сумму.

Разделение ответственности:
  СХЕМА отвергает то, что не может быть значением поля ПО ФОРМЕ;
  АУДИТОР отвергает то, что семантически невозможно (арифметика, хронология,
  статутные лимиты, неподтверждённые реквизиты).
"""

import typing

import pytest

from scan_reader.core.fields import ValidatedPartyMixin, digits_only, empty_str_to_none
from scan_reader.core.finance_parser import parse_russian_currency
from scan_reader.type_registry import get_registry


def _nested(field_info):
    args = typing.get_args(field_info.annotation)
    if args and hasattr(args[0], "model_fields"):
        return args[0]
    if hasattr(field_info.annotation, "model_fields"):
        return field_info.annotation
    return None


def _first_party_model():
    for plugin_id, plugin in sorted(get_registry().enabled().items()):
        for name in ("party_one", "party_two", "seller", "buyer", "customer",
                     "contractor", "principal", "sender", "recipient"):
            if name in plugin.schema_cls.model_fields:
                model = _nested(plugin.schema_cls.model_fields[name])
                if model is not None and "inn" in model.model_fields:
                    return plugin_id, model
    pytest.skip("нет модели стороны с ИНН")


def _salary_finances():
    plugin = get_registry().get("salary_deductions")
    return _nested(plugin.schema_cls.model_fields["finances"])


# =========================================================================
# Валидация идентификаторов
# =========================================================================
@pytest.mark.parametrize("bad", ["7801234567", "7701234567", "7705123456"])
def test_invalid_inn_checksum_rejected(bad):
    """Раньше такая фикстура спокойно проходила: схема ничего не проверяла."""
    _pid, model = _first_party_model()
    with pytest.raises(ValueError):
        model(inn=bad)


@pytest.mark.parametrize("bad", ["780123", "78023127512345", "1234567890123", "не ИНН"])
def test_inn_wrong_length_rejected(bad):
    _pid, model = _first_party_model()
    with pytest.raises(ValueError):
        model(inn=bad)


@pytest.mark.parametrize("good", ["7707083893", "7802312751", "500100732259"])
def test_valid_inn_accepted(good):
    _pid, model = _first_party_model()
    assert model(inn=good).inn == good


def test_inn_with_separators_is_normalised():
    _pid, model = _first_party_model()
    assert model(inn="770 708 389 3").inn == "7707083893"


@pytest.mark.parametrize("bad", ["123", "12345678", "1234567890", "не КПП"])
def test_kpp_wrong_length_rejected(bad):
    _pid, model = _first_party_model()
    with pytest.raises(ValueError):
        model(kpp=bad)


def test_valid_kpp_accepted():
    _pid, model = _first_party_model()
    assert model(kpp="770708389").kpp == "770708389"


def test_empty_string_identifier_is_absent_not_invalid():
    """Пустая строка означает «не заполнено», а не ошибку формата."""
    _pid, model = _first_party_model()
    assert model(inn="", kpp="").inn is None
    assert model(inn="   ").kpp is None


def test_invalid_inn_rejected_across_all_plugins_with_parties():
    """Каждый плагин, объявивший стороны, отвергает невалидный ИНН."""
    for plugin_id, plugin in sorted(get_registry().enabled().items()):
        for name in ("party_one", "party_two", "seller", "buyer", "customer",
                     "contractor", "principal", "sender", "recipient"):
            if name not in plugin.schema_cls.model_fields:
                continue
            model = _nested(plugin.schema_cls.model_fields[name])
            if model is None or "inn" not in model.model_fields:
                continue
            with pytest.raises(ValueError):
                model(inn="7801234567")


# =========================================================================
# Валидация банковских реквизитов
# =========================================================================
def _payment_model():
    plugin = get_registry().get("salary_deductions")
    return _nested(plugin.schema_cls.model_fields["payment_details"])


@pytest.mark.parametrize("good", ["044525225", "015004950"])
def test_valid_bik_accepted(good):
    assert _payment_model()(bik=good).bik == good


@pytest.mark.parametrize("bad", ["123456789", "01500495", "0150049501", "abcde12345"])
def test_invalid_bik_rejected(bad):
    with pytest.raises(ValueError):
        _payment_model()(bik=bad)


@pytest.mark.parametrize("bad", ["12345", "3010181040000000022", "не счёт"])
def test_bank_account_wrong_length_rejected(bad):
    with pytest.raises(ValueError):
        _payment_model()(payment_account=bad)


def test_valid_bank_account_accepted():
    assert _payment_model()(payment_account="30101810400000000225").payment_account == "30101810400000000225"


def test_recipient_inn_control_digit_enforced():
    with pytest.raises(ValueError):
        _payment_model()(recipient_inn="7707083890")
    assert _payment_model()(recipient_inn="7707083893").recipient_inn == "7707083893"


def test_oktmo_length_enforced():
    model = _payment_model()
    assert model(oktmo="65756000").oktmo == "65756000"
    with pytest.raises(ValueError):
        model(oktmo="123")


# =========================================================================
# Денежные поля
# =========================================================================
def test_negative_amount_rejected_not_silently_dropped():
    """
    Раньше parse_russian_currency возвращал None на отрицательном значении, то
    есть VLM, вернувший -150000, получал «поле не заполнено» — неотличимо от
    отсутствия данных.
    """
    assert parse_russian_currency(-150000.0) == -150000.0
    assert parse_russian_currency("-1 000,00") == -1000.0
    finances = _salary_finances()
    with pytest.raises(ValueError):
        finances(debt_amount_rub=-100.0)


def test_money_fields_have_non_negative_constraint():
    """ge=0 объявлен декларативно, а не только через валидатор.

    Числовые *_rub-поля обязаны иметь ge=0. Строковые *_rub-поля (напр.
    periodic_rub с текстовой формулировкой «1/4 части заработка») обязаны
    НЕ иметь числовых ограничений: ge на str-поле в Pydantic v2 даёт
    TypeError при любом непустом значении (баг P0.1 — документы
    с периодическими платежами падали целиком).
    """
    from annotated_types import Ge

    found_numeric = False
    for plugin_id, plugin in sorted(get_registry().enabled().items()):
        if "finances" not in plugin.schema_cls.model_fields:
            continue
        model = _nested(plugin.schema_cls.model_fields["finances"])
        if model is None:
            continue
        for name, info in model.model_fields.items():
            if not name.endswith("_rub"):
                continue
            numeric = info.annotation is float or float in typing.get_args(info.annotation)
            constraints = [c for c in info.metadata if isinstance(c, Ge)]
            if numeric:
                found_numeric = True
                assert constraints and constraints[0].ge == 0, (
                    f"{plugin_id}.{name}: нет ограничения ge=0 (metadata={info.metadata})"
                )
            else:
                assert not constraints, (
                    f"{plugin_id}.{name}: строковое поле *_rub не должно иметь ge=0 "
                    f"(ge на str в Pydantic v2 даёт TypeError; metadata={info.metadata})"
                )
    assert found_numeric, "не найдено ни одного денежного поля"


@pytest.mark.parametrize("value", [0.0, 1000.0, "150 000,00", "1000 руб.", "45.000,00"])
def test_valid_amounts_accepted(value):
    assert parse_russian_currency(value) is not None
    assert _salary_finances()(debt_amount_rub=value).debt_amount_rub is not None


# =========================================================================
# Процент удержания
# =========================================================================
@pytest.mark.parametrize(
    "value,should_pass",
    [
        ("999%", False), ("150%", False), ("abc%", False), ("50%%", False),
        ("-5%", False), ("процентов", True), ("", True),
        ("50%", True), ("70%", True), ("25%", True),
        ("50 процентов", True), ("25 процентов ежемесячно", True),
        ("1/4 части заработка", True), ("1/3 заработка", True), ("1/2 части заработка", True),
        ("четверть", True), ("половина", True),
    ],
)
def test_deduction_percentage_form(value, should_pass):
    """
    Схема проверяет ФОРМУ, аудитор — ПРАВО.

    Проверка «распознаваемости» опирается на тот же parse_percentage_value, что и
    аудитор, иначе реальные формулировки из судебных актов отвергались бы здесь и
    принимались при проверке.
    """
    finances = _salary_finances()
    if should_pass:
        assert finances(deduction_percentage=value).deduction_percentage == value
    else:
        with pytest.raises(ValueError):
            finances(deduction_percentage=value)


def test_schema_and_auditor_agree_on_recognisable_percentages():
    """Расхождение между схемой и аудитором означало бы двойные стандарты."""
    from scan_reader.verifier.math_verifier import parse_percentage_value

    finances = _salary_finances()
    for value in ("50%", "50 процентов", "1/4 части заработка", "четверть", "70%"):
        schema_ok = True
        try:
            finances(deduction_percentage=value)
        except ValueError:
            schema_ok = False
        auditor_reads = parse_percentage_value(value) is not None
        assert schema_ok == auditor_reads or not auditor_reads, (
            f"{value!r}: схема {'приняла' if schema_ok else 'отвергла'}, "
            f"аудитор {'прочитал' if auditor_reads else 'не прочитал'}"
        )


# =========================================================================
# Правило __test__ = False (AGENTS.md Правило 6)
# =========================================================================
def test_root_models_declare_not_a_test():
    """Правило 6 требует __test__ = False у корневых моделей плагинов."""
    for plugin_id, plugin in sorted(get_registry().enabled().items()):
        assert plugin.schema_cls.__test__ is False, (
            f"{plugin_id}: у корневой модели {plugin.schema_cls.__name__} нет __test__ = False"
        )


# =========================================================================
# Хелперы core.fields
# =========================================================================
def test_empty_str_to_none():
    assert empty_str_to_none("") is None
    assert empty_str_to_none("   ") is None
    assert empty_str_to_none("null") == "null"
    assert empty_str_to_none(0) == 0


def test_digits_only():
    assert digits_only("770 708 389 3") == "7707083893"
    assert digits_only("не цифры") is None
    assert digits_only(None) is None


def test_validated_party_mixin_is_base_of_party_models():
    """Стороны наследуют общую валидацию, а не дублируют её."""
    _pid, model = _first_party_model()
    assert issubclass(model, ValidatedPartyMixin)


# =========================================================================
# P0.1 — enforcement_orders: периодические платежи
# =========================================================================
def test_enforcement_periodic_rub_accepts_text_formulation():
    """
    periodic_rub — СТРОКОВОЕ поле («1/4 части заработка»). Раньше на нём висел
    ge=0: Pydantic v2 применяет ge к строке и бросает TypeError при любом
    непустом значении — документ с алиментами падал целиком.
    """
    plugin = get_registry().get("enforcement_orders")
    finances = _nested(plugin.schema_cls.model_fields["finances"])
    assert finances(periodic_rub="1/4 части заработка").periodic_rub == "1/4 части заработка"
    assert finances(periodic_rub="алименты 5000 руб. ежемесячно").periodic_rub.startswith("алименты")
    assert finances().periodic_rub == ""


def test_enforcement_order_doc_validates_full_payload():
    """До P0.1 схема enforcement_orders не была покрыта ни одним тестом (0 %)."""
    plugin = get_registry().get("enforcement_orders")
    doc = plugin.schema_cls.model_validate({
        "file_name": "РОСП_постановление.pdf",
        "doc_type": "Постановление о возбуждении исполнительного производства",
        "doc_date": "01.03.2026",
        "reg_number": "1234/26/77001-ИП",
        "ip_number": "12345/21/77001-ИП",
        "fssp": {"name": "РОСП по ЮЗАО г. Москвы", "region": "Москва", "officer": "Иванов И.И."},
        "court": {"name": "Зюзинский районный суд", "act_date": "15.01.2026", "doc_type": "Исполнительный лист"},
        "claimant": {"name": "ООО «Ромашка»", "details": "ИНН 7707083893, г. Москва"},
        "debtor": {"name": "Иванов Иван Иванович", "details": "10.05.1980, г. Москва"},
        "claim_subject": "взыскание задолженности",
        "finances": {
            "main_debt_rub": 50000.0,
            "court_costs_rub": 2000.0,
            "periodic_rub": "1/4 части заработка",
            "total_rub": 52000.0,
        },
        "has_signature_stamp": True,
    })
    assert doc.ip_number == "12345/21/77001-ИП"
    assert doc.finances.periodic_rub == "1/4 части заработка"
    assert doc.finances.total_rub == 52000.0
    assert doc.has_signature_stamp is True


def test_enforcement_order_doc_rejects_invalid_inn():
    """Стороны на ValidatedPartyMixin: невалидный ИНН отвергается схемой."""
    plugin = get_registry().get("enforcement_orders")
    with pytest.raises(ValueError):
        plugin.schema_cls.model_validate({"debtor": {"name": "ООО «Фиктив»", "inn": "7701234567"}})


# =========================================================================
# P0.2 — powers_of_attorney: ИНН поверенного
# =========================================================================
def test_poa_agent_empty_inn_is_none_not_error():
    """
    Раньше AttorneyAgentInfo переобъявлял inn как str поверх миксина:
    before-валидатор миксина превращал "" в None, аннотация str отвергала
    None — документ доверенности с незаполненным ИНН поверенного (частая
    ситуация) падал целиком ValidationError'ом.
    """
    plugin = get_registry().get("powers_of_attorney")
    model = _nested(plugin.schema_cls.model_fields["agent"])
    assert model(inn="").inn is None
    assert model(inn="7707083893").inn == "7707083893"
    with pytest.raises(ValueError):
        model(inn="7701234567")


def test_poa_full_document_with_empty_agent_inn_validates():
    doc = get_registry().get("powers_of_attorney").schema_cls.model_validate({
        "issue_date": "01.03.2026",
        "valid_until": "01.03.2029",
        "principal": {"name": "ООО «Ромашка»", "inn": "7707083893"},
        "agent": {"full_name": "Сидоров С.С.", "inn": ""},
        "is_notarized": True,
    })
    assert doc.agent.inn is None
    assert doc.principal.inn == "7707083893"


# =========================================================================
# P0.3 — salary_deductions: банковский блок и реквизиты работодателя
# =========================================================================
def test_salary_bank_blank_fields_are_none_not_error():
    """
    PaymentDetailsInfo переобъявлял bik/payment_account как str поверх
    ValidatedBankMixin ("" -> None отвергался аннотацией str) и держал
    одноимённый _blank_to_none, который в Pydantic v2 ЗАМЕЩАЛ миксиновый
    валидатор. Пустые банковские реквизиты валили документ целиком.
    """
    model = _payment_model()
    pd = model(bik="", payment_account="")
    assert pd.bik is None and pd.payment_account is None
    pd2 = model(bik="015004950", payment_account="03212643000000015113")
    assert pd2.bik == "015004950" and pd2.payment_account == "03212643000000015113"


def test_salary_blank_recipient_inn_is_none_not_error():
    """Тот же паттерн на recipient_inn/recipient_kpp: str + ""->None = падение."""
    model = _payment_model()
    assert model(recipient_inn="", recipient_kpp="").recipient_inn is None
    assert model(recipient_inn="", recipient_kpp="").recipient_kpp is None


def test_salary_employer_inn_identifier_check_is_alive():
    """
    verification.json salary_deductions объявляет identifier_checks
    employer.inn — до P0.3 поля не существовало и проверка была мёртвой.
    """
    plugin = get_registry().get("salary_deductions")
    employer = _nested(plugin.schema_cls.model_fields["employer"])
    assert "inn" in employer.model_fields
    assert employer(inn="").inn is None
    assert employer(inn="7707083893").inn == "7707083893"
    with pytest.raises(ValueError):
        employer(inn="7701234567")


def test_salary_full_document_with_blank_bank_details_validates():
    doc = get_registry().get("salary_deductions").schema_cls.model_validate({
        "employer": {"name": "ООО «Работодатель»", "inn": ""},
        "payment_details": {"bik": "", "payment_account": "", "recipient_inn": ""},
        "finances": {"debt_amount_rub": 45000.0, "total_deduction_rub": 45000.0, "deduction_percentage": "50%"},
    })
    assert doc.payment_details.bik is None
    assert doc.employer.inn is None


# =========================================================================
# P0.4 — commercial_contracts: сверка цены договора
# =========================================================================
def test_commercial_contract_money_rule_is_alive():
    """
    До P0.4 слагаемое finances.total_rub_no_vat из verification.json
    отсутствовало в ContractFinances: правило (total = no_vat + vat,
    min_components=2) не набирало двух компонентов и сверка цены договора
    НИКОГДА не выполнялась — сердце Правила 2.
    """
    from scan_reader.verifier.auditor import ZeroTrustAuditor

    plugin = get_registry().get("commercial_contracts")
    finances = _nested(plugin.schema_cls.model_fields["finances"])
    assert "total_rub_no_vat" in finances.model_fields

    consistent = {
        "finances": {"total_rub": 125000.0, "total_rub_no_vat": 104166.67, "vat_amount_rub": 20833.33}
    }
    report = ZeroTrustAuditor.audit_document(consistent, doc_type="commercial_contracts")
    codes = [i.code for i in report.issues]
    assert "CONTRACT_TOTAL_DISCREPANCY" not in codes

    inconsistent = {
        "finances": {"total_rub": 150000.0, "total_rub_no_vat": 104166.67, "vat_amount_rub": 20833.33}
    }
    report_bad = ZeroTrustAuditor.audit_document(inconsistent, doc_type="commercial_contracts")
    assert any(i.code == "CONTRACT_TOTAL_DISCREPANCY" for i in report_bad.issues)


# =========================================================================
# P1.9: оставшиеся ограничения форм (Rule 6)
# =========================================================================
def _hr_employee():
    plugin = get_registry().get("hr_orders")
    return _nested(plugin.schema_cls.model_fields["employee"])


def test_p19_hr_employee_inn_from_party_mixin():
    """ИНН работника наследует ValidatedPartyMixin: форма и чексумма ФНС."""
    model = _hr_employee()
    assert model(inn="").inn is None
    assert model(inn="7707083893").inn == "7707083893"
    with pytest.raises(ValueError):
        model(inn="123")          # не 10/12 цифр
    with pytest.raises(ValueError):
        model(inn="7701234567")   # неверная контрольная сумма


def test_p19_hr_employee_snils_form():
    """СНИЛС работника: 11 цифр, пустое значение пропускается."""
    model = _hr_employee()
    assert model(snils="").snils == ""
    assert model(snils="112-233-445 95").snils == "112-233-445 95"
    with pytest.raises(ValueError):
        model(snils="123-45")  # мало цифр
    with pytest.raises(ValueError):
        model(snils="123456789012")  # 12 цифр — не СНИЛС


def test_p19_hr_organization_inn_form():
    """ИНН организации в корне приказа: 10/12 цифр, пустое пропускается."""
    plugin = get_registry().get("hr_orders")
    doc_cls = plugin.schema_cls
    assert doc_cls(organization_inn="").organization_inn == ""
    assert doc_cls(organization_inn="7707083893").organization_inn == "7707083893"
    with pytest.raises(ValueError):
        doc_cls(organization_inn="77070838931")  # 11 цифр


def test_p19_upd_item_amounts_are_non_negative():
    """Построчные суммы УПД: отрицательные значения — ошибка формы."""
    plugin = get_registry().get("invoices_upd")
    item = _nested(plugin.schema_cls.model_fields["items"])
    with pytest.raises(ValueError):
        item(price=-100.0)
    with pytest.raises(ValueError):
        item(quantity=-2.0)
    with pytest.raises(ValueError):
        item(vat_amount=-0.01)
    assert item(price=100.0).price == 100.0
    finances = _nested(plugin.schema_cls.model_fields["finances"])
    with pytest.raises(ValueError):
        finances(total_rub_no_vat=-1.0)
    assert finances(total_rub_no_vat=100.0).total_rub_no_vat == 100.0


def test_p19_commercial_contract_ogrn_form():
    """ОГРН контрагента: 13 или 15 цифр, пустое пропускается."""
    plugin = get_registry().get("commercial_contracts")
    party = _nested(plugin.schema_cls.model_fields["party_one"])
    assert party(ogrn="").ogrn == ""
    assert party(ogrn="1027700132195").ogrn == "1027700132195"      # 13
    assert party(ogrn="304774612345678").ogrn == "304774612345678"  # 15 (ОГРНИП)
    with pytest.raises(ValueError):
        party(ogrn="102770013219")   # 12 цифр
    with pytest.raises(ValueError):
        party(ogrn="ОГРН 123")       # не цифры


def test_p19_poa_agent_snils_form():
    """СНИЛС представителя в доверенности: 11 цифр, пустое пропускается."""
    plugin = get_registry().get("powers_of_attorney")
    agent = _nested(plugin.schema_cls.model_fields["agent"])
    assert agent(snils="").snils == ""
    assert agent(snils="112-233-445 95").snils == "112-233-445 95"
    with pytest.raises(ValueError):
        agent(snils="1234567890")  # 10 цифр — не СНИЛС


def test_p19_executive_blank_form():
    """Бланк исполнительного листа: серия — буквы, номер — 6-12 цифр."""
    plugin = get_registry().get("executive_documents")
    doc_cls = plugin.schema_cls
    assert doc_cls(blank_series="", blank_number="").blank_series == ""
    assert doc_cls(blank_series="ФС", blank_number="123456789").blank_number == "123456789"
    with pytest.raises(ValueError):
        doc_cls(blank_series="ФС007")   # серия с цифрами
    with pytest.raises(ValueError):
        doc_cls(blank_number="123")    # слишком короткий номер
    with pytest.raises(ValueError):
        doc_cls(blank_number="123456789012345")  # 15 цифр — не бланк

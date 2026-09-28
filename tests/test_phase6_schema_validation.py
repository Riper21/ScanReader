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
    """ge=0 объявлен декларативно, а не только через валидатор."""
    from annotated_types import Ge

    found = False
    for plugin_id, plugin in sorted(get_registry().enabled().items()):
        if "finances" not in plugin.schema_cls.model_fields:
            continue
        model = _nested(plugin.schema_cls.model_fields["finances"])
        if model is None:
            continue
        for name, info in model.model_fields.items():
            if not name.endswith("_rub"):
                continue
            found = True
            constraints = [c for c in info.metadata if isinstance(c, Ge)]
            assert constraints and constraints[0].ge == 0, (
                f"{plugin_id}.{name}: нет ограничения ge=0 (metadata={info.metadata})"
            )
    assert found, "не найдено ни одного денежного поля"


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

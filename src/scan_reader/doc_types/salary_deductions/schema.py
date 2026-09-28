# -*- coding: utf-8 -*-
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator
from scan_reader.core.fields import (
    BIK_PATTERN,
    INN_PATTERN,
    KPP_PATTERN,
    ValidatedBankMixin,
    digits_only,
    empty_str_to_none,
)
from scan_reader.core.finance_parser import parse_russian_currency
from scan_reader.core.utils import coerce_to_str, normalize_ip_number


class AuthorityInfo(BaseModel):


    name: str = Field(default="", description="Наименование органа принудительного исполнения (ОСП/РОСП)")
    jurisdiction: str = Field(default="Российская Федерация", description="Юрисдикция (РФ, РК и др.)")
    address: str = Field(default="", description="Адрес подразделения судебных приставов")
    officer: str = Field(default="", description="ФИО и должность судебного пристава-исполнителя")

    @field_validator('name', 'jurisdiction', 'address', 'officer', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class EmployerInfo(BaseModel):
    name: str = Field(default="", description="Наименование работодателя должника / организации")
    address: str = Field(default="", description="Адрес работодателя / бухгалтерии")

    @field_validator('name', 'address', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class DeductionFinances(BaseModel):
    debt_amount_rub: Optional[float] = Field(default=None, ge=0, description="Основная задолженность (руб.)")
    fee_penalty_rub: Optional[float] = Field(default=None, ge=0, description="Госпошлина / проценты (руб.)")
    total_deduction_rub: Optional[float] = Field(default=None, ge=0, description="Итого к удержанию (руб.)")
    deduction_percentage: str = Field(default="", description="Процент удержания (число, «50 процентов», «1/4 части»)")
    periodic_details: str = Field(default="", description="Периодичность и периодическая сумма удержания")

    @field_validator('debt_amount_rub', 'fee_penalty_rub', 'total_deduction_rub', mode='before')
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)

    @field_validator('deduction_percentage', 'periodic_details', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

    @field_validator('deduction_percentage', mode='after')
    @classmethod
    def _check_percentage(cls, v: str) -> str:
        """
        Схема отвергает ЗАВЕДОМО непригодную запись и выход за 0..100 %.

        Распознаваемость определяется ТЕМ ЖЕ парсером, что использует аудитор
        (math_verifier.parse_percentage_value). Иначе формулировки, реально
        встречающиеся в судебных актах («50 процентов», «1/4 части заработка»),
        отвергались бы схемой, но принимались бы при проверке.

        Домысливать процент нельзя: из 999 молча превратилось бы 0. Юридическую
        оценку допустимых 50/70 % по ст. 99 229-ФЗ делает аудитор.
        """
        if not v.strip():
            return v
        import re

        from scan_reader.verifier.math_verifier import parse_percentage_value

        # Проверки ФОРМЫ выполняются по исходной строке: парсер умеет вытащить
        # правдоподобное число из мусора («-5%» прочтётся как 5 %, «50%%» — как
        # 50 %), и формальные браки прошли бы незамеченными.
        if v.count("%") > 1:
            raise ValueError(f"Процент удержания содержит повторяющийся знак «%»: {v!r}")
        if re.search(r"(^|\s)-\s*\d", v):
            raise ValueError(
                f"Процент удержания отрицателен, что невозможно по существу: {v!r}"
            )

        value = parse_percentage_value(v)
        if value is None:
            # Голое число без знака «%» («50») — обычная запись VLM, принимается.
            bare = v.replace(" ", "").replace(",", ".").rstrip("%")
            try:
                value = float(bare)
            except ValueError:
                # Словесные формы без цифр («четверть», «половина») допустимы:
                # это не брак формы, а вопрос права, который решает аудитор.
                if re.search(r"\d", v) or "%" in v:
                    raise ValueError(
                        f"Процент удержания записан нераспознаваемо: {v!r}. Ожидается число "
                        "(«50%», «50 процентов») или доля («1/4 части заработка»)."
                    ) from None
                return v
        if value < 0 or value > 100:
            raise ValueError(
                f"Процент удержания должен быть в диапазоне 0..100, получено {value} %: {v!r}"
            )
        return v


class PaymentDetailsInfo(ValidatedBankMixin):
    bik: str = Field(default="", description="БИК банка (9 цифр, например '015004950')")
    payment_account: str = Field(default="", description="Расчётный счёт / лицевой счёт (20 цифр, например '03212643000000015113')")
    recipient: str = Field(default="", description="Полное наименование получателя платежа (УФК/банк)")
    recipient_inn: str = Field(default="", description="ИНН получателя (10 или 12 цифр)")
    recipient_kpp: str = Field(default="", description="КПП получателя (9 цифр)")
    oktmo: str = Field(default="", description="Код ОКТМО (8 или 11 цифр, например '65756000')")
    uin: str = Field(default="", description="УИН - уникальный идентификатор платежа (20-25 цифр)")
    rosp_code: str = Field(default="", description="5-значный код ведомственного органа получателя, например '66050'")

    @field_validator('recipient', 'uin', 'rosp_code', 'oktmo', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

    @field_validator('recipient_inn', 'recipient_kpp', mode='before')
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return empty_str_to_none(v)

    @field_validator('recipient_inn', mode='after')
    @classmethod
    def _check_inn(cls, v: Any) -> Any:
        if v is None:
            return None
        cleaned = digits_only(v)
        if cleaned is None or not __import__("re").match(INN_PATTERN, cleaned):
            raise ValueError(
                f"ИНН получателя должен содержать 10 или 12 цифр, получено: {v!r}"
            )
        from scan_reader.verifier.checksums import validate_inn

        ok, msg = validate_inn(cleaned)
        if not ok:
            raise ValueError(f"ИНН получателя {cleaned} не проходит контрольную сумму ФНС: {msg}")
        return cleaned

    @field_validator('recipient_kpp', mode='after')
    @classmethod
    def _check_kpp(cls, v: Any) -> Any:
        if v is None:
            return None
        cleaned = digits_only(v)
        if cleaned is None or not __import__("re").match(KPP_PATTERN, cleaned):
            raise ValueError(f"КПП получателя должен содержать 9 цифр, получено: {v!r}")
        return cleaned

    @field_validator('oktmo', mode='after')
    @classmethod
    def _check_oktmo(cls, v: str) -> str:
        if not v.strip():
            return v
        cleaned = digits_only(v)
        if cleaned is None or len(cleaned) not in (8, 11):
            raise ValueError(
                f"ОКТМО должен содержать 8 или 11 цифр, получено {len(cleaned) if cleaned else 0}: {v!r}"
            )
        return cleaned

    @field_validator('bik', 'payment_account', mode='before')
    @classmethod
    def _bank_blank_to_none(cls, v: Any) -> Any:
        return empty_str_to_none(v)

    @field_validator('bik', mode='after')
    @classmethod
    def _check_bik_format(cls, v: Any) -> Any:
        if v is None:
            return None
        cleaned = digits_only(v)
        if cleaned is None or not __import__("re").match(BIK_PATTERN, cleaned):
            raise ValueError(
                f"БИК должен содержать 9 цифр и начинаться с 04 (банк) "
                f"или 01 (казначейство), получено: {v!r}"
            )
        return cleaned

    @field_validator('payment_account', mode='after')
    @classmethod
    def _check_account_digits(cls, v: Any) -> Any:
        if v is None:
            return None
        cleaned = digits_only(v)
        if cleaned is None or len(cleaned) != 20:
            raise ValueError(f"Банковский счёт должен содержать 20 цифр, получено: {v!r}")
        return cleaned


class SalaryDeductionDoc(BaseModel):
    __test__ = False
    file_name: str = Field(default="", description="Имя файла документа")
    doc_type: str = Field(default="Постановление об обращении взыскания на заработную плату и иные доходы должника", description="Тип документа")
    doc_date: str = Field(default="", description="Дата вынесения постановления")
    doc_number: str = Field(default="", description="Номер постановления (штрихкод/код)")
    ip_number: str = Field(default="", description="Номер исполнительного производства")
    ip_date: str = Field(default="", description="Дата возбуждения исполнительного производства")
    authority: AuthorityInfo = Field(default_factory=AuthorityInfo)
    base_doc: str = Field(default="", description="Исполнительный документ-основание (судебный приказ, ИЛ)")
    base_doc_number: str = Field(default="", description="Номер исполнительного документа-основания (например, ФС00001234, 2-967)")
    base_doc_date: str = Field(default="", description="Дата исполнительного документа-основания (например, 20.01.2016)")
    court_name: str = Field(default="", description="Судебный орган, выдавший основание")
    claimant_name: str = Field(default="", description="Наименование или ФИО взыскателя")
    claimant_type: str = Field(default="", description="Тип взыскателя (ФНС, Банк, Физлицо-алименты)")
    claimant_details: str = Field(default="", description="Банковские реквизиты, расчетный счет взыскателя")
    debtor_name: str = Field(default="", description="ФИО должника-работника")
    debtor_details: str = Field(default="", description="ИНН, СНИЛС, дата рождения, адрес должника")
    employer: EmployerInfo = Field(default_factory=EmployerInfo)
    claim_subject: str = Field(default="", description="Предмет взыскания (налоги, кредит, алименты)")
    legal_base: str = Field(default="", description="Статьи закона (ст. 98, 99 ФЗ № 229-ФЗ)")
    finances: DeductionFinances = Field(default_factory=DeductionFinances)
    payment_details: PaymentDetailsInfo = Field(default_factory=PaymentDetailsInfo, description="Банковские и казначейские реквизиты для перечисления удержаний (БИК, счет 032..., УИН, ОКТМО, получатель УФК)")
    is_anonymized: bool = Field(default=False, description="Признак деперсонализации / замазанных персональных данных")
    has_signature_stamp: bool = Field(default=True, description="Наличие подписи пристава/ЧСИ и гербовой печати / ЭЦП")
    handwritten_elements: bool = Field(default=False, description="Наличие рукописных записей или пометок")
    notes: str = Field(default="", description="Особые отметки и требования к бухгалтерии")

    @field_validator(
        'file_name', 'doc_type', 'doc_date', 'doc_number', 'ip_number', 'ip_date',
        'base_doc', 'base_doc_number', 'base_doc_date', 'court_name',
        'claimant_name', 'claimant_type', 'claimant_details',
        'debtor_name', 'debtor_details', 'claim_subject', 'legal_base', 'notes',
        mode='before'
    )
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

    @field_validator('doc_number', 'ip_number', mode='before')
    @classmethod
    def restore_ip_format(cls, v: Any) -> str:
        """Восстановление канонического формата NNNNN/NN/NNNNN-ИП из слитных цифр."""
        return normalize_ip_number(v)


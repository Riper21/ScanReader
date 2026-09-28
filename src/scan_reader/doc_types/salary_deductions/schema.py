# -*- coding: utf-8 -*-
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator
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
    debt_amount_rub: Optional[float] = Field(default=None, description="Сумма задолженности (руб)")
    fee_penalty_rub: Optional[float] = Field(default=None, description="Исполнительский сбор / штраф (руб)")
    total_deduction_rub: Optional[float] = Field(default=None, description="Общая сумма удержания (руб)")
    deduction_percentage: str = Field(default="", description="Процент удержания (например, 50% ежемесячно, 25% алименты)")
    periodic_details: str = Field(default="", description="Порядок и сроки перечисления удержанных сумм")

    @field_validator('debt_amount_rub', 'fee_penalty_rub', 'total_deduction_rub', mode='before')
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)

    @field_validator('deduction_percentage', 'periodic_details', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class PaymentDetailsInfo(BaseModel):
    bik: str = Field(default="", description="БИК банка ТОФК / казначейства (9 цифр, например '015004950')")
    payment_account: str = Field(default="", description="Номер счета казначейства / расчетного счета (20 цифр, например '03212643000000015113')")
    recipient: str = Field(default="", description="Официальное наименование получателя платежа для казначейства (УФК по...)")
    recipient_inn: str = Field(default="", description="ИНН получателя (10 цифр)")
    recipient_kpp: str = Field(default="", description="КПП получателя (9 цифр)")
    oktmo: str = Field(default="", description="Код ОКТМО (8 или 11 цифр, например '65756000')")
    uin: str = Field(default="", description="УИН — Уникальный идентификатор начисления (20-25 цифр, например '32266050260610077000')")
    rosp_code: str = Field(default="", description="5-значный ведомственный код подразделения РОСП (например, '66050')")

    @field_validator('bik', 'payment_account', 'recipient', 'recipient_inn', 'recipient_kpp', 'oktmo', 'uin', 'rosp_code', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


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



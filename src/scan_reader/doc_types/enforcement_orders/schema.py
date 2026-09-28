# -*- coding: utf-8 -*-
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator
from scan_reader.core.finance_parser import parse_russian_currency
from scan_reader.core.utils import coerce_to_str, normalize_ip_number


class FsspInfo(BaseModel):
    name: str = Field(default="", description="Наименование подразделения ФССП/РОСП")
    region: str = Field(default="", description="Регион / город подразделения ФССП")
    address: str = Field(default="", description="Адрес РОСП/УФССП")
    officer: str = Field(default="", description="ФИО и должность судебного пристава-исполнителя")

    @field_validator('name', 'region', 'address', 'officer', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class CourtInfo(BaseModel):
    name: str = Field(default="", description="Наименование суда, выдавшего исполнительный документ")
    case_number: str = Field(default="", description="Номер судебного дела")
    act_date: str = Field(default="", description="Дата судебного акта и вступления в силу")
    doc_type: str = Field(default="", description="Тип исполнительного документа (Исполнительный лист, Судебный приказ)")
    blank_number: str = Field(default="", description="Серия и номер бланка")

    @field_validator('name', 'case_number', 'act_date', 'doc_type', 'blank_number', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class PartyInfo(BaseModel):
    name: str = Field(default="", description="Наименование или ФИО стороны")
    party_type: str = Field(default="", description="Тип стороны (Физлицо, Юрлицо, Муниципальный орган)")
    details: str = Field(default="", description="Реквизиты, адрес, паспортные данные, ИНН")
    representative: str = Field(default="", description="Представитель по доверенности")

    @field_validator('name', 'party_type', 'details', 'representative', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class FinancesInfo(BaseModel):
    main_debt_rub: Optional[float] = Field(default=None, description="Основной долг в рублях")
    court_costs_rub: Optional[float] = Field(default=None, description="Судебные расходы / пошлина в рублях")
    periodic_rub: str = Field(default="", description="Периодические платежи")
    fee_penalty: str = Field(default="", description="Исполнительский сбор / штраф")
    total_rub: Optional[float] = Field(default=None, description="Итоговая сумма к взысканию в рублях")
    non_monetary_summary: str = Field(default="", description="Предмет неимущественного требования")

    @field_validator('main_debt_rub', 'court_costs_rub', 'total_rub', mode='before')
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)

    @field_validator('periodic_rub', 'fee_penalty', 'non_monetary_summary', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class EnforcementOrderDoc(BaseModel):
    __test__ = False
    file_name: str = Field(default="", description="Имя файла документа")
    doc_type: str = Field(default="Заявление взыскателя о возбуждении ИП", description="Тип документа")
    doc_date: str = Field(default="", description="Дата документа")
    reg_number: str = Field(default="", description="Входящий или регистрационный номер")
    ip_number: str = Field(default="", description="Номер исполнительного производства (если присвоен)")
    fssp: FsspInfo = Field(default_factory=FsspInfo)
    court: CourtInfo = Field(default_factory=CourtInfo)
    claimant: PartyInfo = Field(default_factory=PartyInfo)
    debtor: PartyInfo = Field(default_factory=PartyInfo)
    claim_subject: str = Field(default="", description="Предмет требования / взыскания")
    legal_base: str = Field(default="", description="Нормативно-правовое основание (статьи ФЗ)")
    finances: FinancesInfo = Field(default_factory=FinancesInfo)
    has_signature_stamp: bool = Field(default=False, description="Наличие подписи и печати/штампа")
    is_anonymized: bool = Field(default=False, description="Наличие цензурированных/замазанных персональных данных")
    handwritten_elements: bool = Field(default=False, description="Наличие рукописных записей или пометок")
    notes: str = Field(default="", description="Примечания и особые отметки")

    @field_validator(
        'file_name', 'doc_type', 'doc_date', 'reg_number', 'ip_number',
        'claim_subject', 'legal_base', 'notes',
        mode='before'
    )
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

    @field_validator('ip_number', mode='before')
    @classmethod
    def restore_ip_format(cls, v: Any) -> str:
        """Восстановление канонического формата NNNNN/NN/NNNNN-ИП из слитных цифр."""
        return normalize_ip_number(v)


# -*- coding: utf-8 -*-
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator
from scan_reader.core.finance_parser import parse_russian_currency
from scan_reader.core.fields import ValidatedPartyMixin
from scan_reader.core.utils import coerce_to_str


class CourtDetails(BaseModel):


    name: str = Field(default="", description="Наименование суда")
    address: str = Field(default="", description="Адрес суда")
    court_type: str = Field(default="", description="Тип суда (Арбитражный, Районный, Мировой)")
    base_decision: str = Field(default="", description="Решение/судебный акт, на основании которого выдан лист")

    @field_validator('name', 'address', 'court_type', 'base_decision', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class PartyDetails(ValidatedPartyMixin):
    name: str = Field(default="", description="Наименование юрлица или ФИО физлица")
    party_type: str = Field(default="", description="Тип стороны (Юрлицо, Физлицо, ИП, Гос. орган)")
    details: str = Field(default="", description="Реквизиты, ИНН, ОГРН, адрес, паспортные данные")

    @field_validator('name', 'party_type', 'details', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class ExecFinances(BaseModel):
    main_debt_rub: Optional[float] = Field(ge=0, default=None, description="Сумма основного долга")
    interest_penalty_rub: Optional[float] = Field(ge=0, default=None, description="Проценты / пени / неустойка")
    court_fee_rub: Optional[float] = Field(ge=0, default=None, description="Госпошлина / третейский сбор")
    other_rub: Optional[float] = Field(ge=0, default=None, description="Иные расходы")
    total_rub: Optional[float] = Field(ge=0, default=None, description="Итоговая сумма к взысканию")
    non_monetary: str = Field(default="", description="Требования неимущественного характера")

    @field_validator('main_debt_rub', 'interest_penalty_rub', 'court_fee_rub', 'other_rub', 'total_rub', mode='before')
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)

    @field_validator('non_monetary', mode='before')
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class ExecutiveDocumentDoc(BaseModel):
    __test__ = False
    file_name: str = Field(default="", description="Имя файла документа")
    doc_type: str = Field(default="Исполнительный лист", description="Тип документа")
    case_number: str = Field(default="", description="Номер судебного дела")
    act_date: str = Field(default="", description="Дата принятия судебного акта или вступления в силу")
    court: CourtDetails = Field(default_factory=CourtDetails)
    claimant: PartyDetails = Field(default_factory=PartyDetails)
    debtor: PartyDetails = Field(default_factory=PartyDetails)
    claim_subject: str = Field(default="", description="Предмет иска / требования")
    decision_summary: str = Field(default="", description="Резолютивная часть решения суда")
    finances: ExecFinances = Field(default_factory=ExecFinances)
    blank_series: str = Field(default="", description="Серия бланка (ФС, ВС)")
    blank_number: str = Field(default="", description="Номер бланка строгой отчетности")
    handwritten_elements: bool = Field(default=False, description="Наличие рукописных записей или исправлений")
    is_anonymized: bool = Field(default=False, description="Признак деперсонализированного документа")
    has_signature_stamp: bool = Field(default=True, description="Наличие гербовой печати суда и подписи судьи/секретаря")

    @field_validator(
        'file_name', 'doc_type', 'case_number', 'act_date', 'claim_subject',
        'decision_summary', 'blank_series', 'blank_number',
        mode='before'
    )
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

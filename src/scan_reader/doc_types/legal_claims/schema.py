# -*- coding: utf-8 -*-
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator
from scan_reader.core.finance_parser import parse_russian_currency
from scan_reader.core.utils import coerce_to_str


class PartyClaimInfo(BaseModel):
    name: str = Field(default="", description="Наименование организации или ФИО стороны")
    inn: str = Field(default="", description="ИНН стороны (10 или 12 цифр)")
    kpp: str = Field(default="", description="КПП организации")
    address: str = Field(default="", description="Адрес местонахождения / регистрации")
    signatory_fio: str = Field(default="", description="ФИО подписанта")

    @field_validator("name", "inn", "kpp", "address", "signatory_fio", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class ClaimFinances(BaseModel):
    principal_debt_rub: Optional[float] = Field(default=None, description="Сумма основного долга (руб)")
    penalty_rub: Optional[float] = Field(default=None, description="Сумма пени / неустойки (руб)")
    interest_rub: Optional[float] = Field(default=None, description="Проценты за пользование чужими денежными средствами (ст. 395 ГК РФ)")
    total_claim_rub: Optional[float] = Field(default=None, description="Общая сумма претензионных требований (руб)")
    currency: str = Field(default="RUB", description="Валюта требований")

    @field_validator("principal_debt_rub", "penalty_rub", "interest_rub", "total_claim_rub", mode="before")
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)

    @field_validator("currency", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class LegalClaimDoc(BaseModel):
    doc_number: str = Field(default="", description="Исходящий номер претензии")
    doc_date: str = Field(default="", description="Дата составления претензии (DD.MM.YYYY)")
    city: str = Field(default="", description="Город составления претензии")
    sender: PartyClaimInfo = Field(default_factory=PartyClaimInfo, description="Заявитель претензии (Кредитор)")
    recipient: PartyClaimInfo = Field(default_factory=PartyClaimInfo, description="Получатель претензии (Должник)")
    contract_basis_number: str = Field(default="", description="Номер договора-основания")
    contract_basis_date: str = Field(default="", description="Дата договора-основания")
    claim_subject: str = Field(default="", description="Краткое существо нарушенных обязательств")
    finances: ClaimFinances = Field(default_factory=ClaimFinances, description="Суммы финансовых требований")
    response_deadline_days: Optional[int] = Field(default=None, description="Срок ответа на претензию в календарных/рабочих днях")
    response_deadline_date: str = Field(default="", description="Точная дата дедлайна ответа")
    bank_requisites: str = Field(default="", description="Банковские реквизиты для перечисления средств")
    has_signature_stamp: bool = Field(default=True, description="Наличие подписи уполномоченного лица и печати")

    @field_validator("doc_number", "doc_date", "city", "contract_basis_number", "contract_basis_date", "claim_subject", "response_deadline_date", "bank_requisites", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

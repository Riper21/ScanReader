# -*- coding: utf-8 -*-
import re
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator
from scan_reader.core.finance_parser import parse_russian_currency
from scan_reader.core.fields import ValidatedBankMixin, ValidatedPartyMixin
from scan_reader.core.utils import coerce_to_str


class PartyClaimInfo(ValidatedPartyMixin):
    name: str = Field(default="", description="Наименование организации или ФИО стороны")
    address: str = Field(default="", description="Адрес местонахождения / регистрации")
    signatory_fio: str = Field(default="", description="ФИО подписанта")

    @field_validator("name", "inn", "kpp", "address", "signatory_fio", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class BankRequisitesInfo(ValidatedBankMixin):
    """
    P1.4: банковский блок претензии — структурированная модель вместо строки.

    Раньше поле было строкой, и все проверки verification.json
    (bank_requisites.bik, .account, .kpp, .oktmo) указывали на несуществующие
    пути — контрольные суммы БИК и ключ счёта не работали никогда.
    """

    bank_name: str = Field(default="", description="Наименование банка получателя")
    kpp: Optional[str] = Field(default=None, description="КПП получателя (9 цифр)")
    oktmo: Optional[str] = Field(default=None, description="ОКТМО получателя")
    recipient: str = Field(default="", description="Получатель платежа (для счетов Банка России/ГРКЦ)")

    @field_validator("bank_name", "recipient", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

    @field_validator("kpp", "oktmo", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        if isinstance(v, str) and not v.strip():
            return None
        return v


def _parse_bank_requisites(v: Any) -> Any:
    """
    Обратная совместимость: модель может прийти строкой старого формата
    («р/с 40702810..., БИК 044525...»), как её извлекала модель раньше.

    Структурированные реквизиты вытаскиваются регулярными выражениями,
    остальной текст сохраняется в bank_name.
    """
    if isinstance(v, str):
        blob = v.strip()
        if not blob:
            return {}
        parsed: dict = {}
        account = re.search(r"(?:р/с|р/сч|расч\.?\s*счет)[^\d]{0,10}(\d{20})", blob, re.IGNORECASE)
        if account:
            parsed["payment_account"] = account.group(1)
        bik = re.search(r"БИК[^\d]{0,10}(\d{9})", blob, re.IGNORECASE)
        if bik:
            parsed["bik"] = bik.group(1)
        kpp = re.search(r"КПП[^\d]{0,10}(\d{9})", blob, re.IGNORECASE)
        if kpp:
            parsed["kpp"] = kpp.group(1)
        bank = re.search(r"(?:в\s+)?(?:ПАО|АО|ЗАО|ОАО|Банк|банк)\s+[А-ЯЁA-Za-z«»\s\-]+", blob)
        if bank:
            parsed["bank_name"] = bank.group(0).strip()
        return parsed
    return v


class ClaimFinances(BaseModel):


    principal_debt_rub: Optional[float] = Field(ge=0, default=None, description="Сумма основного долга (руб)")
    penalty_rub: Optional[float] = Field(ge=0, default=None, description="Сумма пени / неустойки (руб)")
    interest_rub: Optional[float] = Field(ge=0, default=None, description="Проценты за пользование чужими денежными средствами (ст. 395 ГК РФ)")
    total_claim_rub: Optional[float] = Field(ge=0, default=None, description="Общая сумма претензионных требований (руб)")
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
    __test__ = False
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
    bank_requisites: BankRequisitesInfo = Field(
        default_factory=BankRequisitesInfo,
        description="Банковские реквизиты для перечисления средств (счёт, БИК, банк)",
    )
    has_signature_stamp: bool = Field(default=True, description="Наличие подписи уполномоченного лица и печати")

    @field_validator("doc_number", "doc_date", "city", "contract_basis_number", "contract_basis_date", "claim_subject", "response_deadline_date", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

    @field_validator("bank_requisites", mode="before")
    @classmethod
    def parse_bank_block(cls, v: Any) -> Any:
        return _parse_bank_requisites(v)

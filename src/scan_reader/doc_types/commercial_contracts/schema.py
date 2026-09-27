# -*- coding: utf-8 -*-
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator
from scan_reader.core.finance_parser import parse_russian_currency
from scan_reader.core.utils import coerce_to_str


class PartyInfo(BaseModel):
    name: str = Field(default="", description="Наименование организации или ФИО контрагента")
    role: str = Field(default="", description="Роль стороны (Заказчик, Исполнитель, Поставщик, Покупатель, Арендатор и др.)")
    inn: str = Field(default="", description="ИНН контрагента (10 или 12 цифр)")
    kpp: str = Field(default="", description="КПП организации (9 цифр)")
    ogrn: str = Field(default="", description="ОГРН / ОГРНИП")
    signatory_fio: str = Field(default="", description="ФИО уполномоченного лица / подписанта")
    signatory_basis: str = Field(default="", description="Основание полномочий (Устав, Доверенность №...)")

    @field_validator("name", "role", "inn", "kpp", "ogrn", "signatory_fio", "signatory_basis", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class ContractFinances(BaseModel):
    total_rub: Optional[float] = Field(default=None, description="Общая цена/сумма договора (руб)")
    vat_included: Optional[bool] = Field(default=None, description="Включен ли НДС в сумму (true/false)")
    vat_rate: str = Field(default="", description="Ставка НДС (20%, 10%, 0%, Без НДС)")
    vat_amount_rub: Optional[float] = Field(default=None, description="Сумма НДС (руб)")
    currency: str = Field(default="RUB", description="Валюта договора")

    @field_validator("total_rub", "vat_amount_rub", mode="before")
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)

    @field_validator("vat_rate", "currency", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class CommercialContractDoc(BaseModel):
    doc_number: str = Field(default="", description="Номер договора")
    doc_date: str = Field(default="", description="Дата заключения договора (DD.MM.YYYY)")
    city: str = Field(default="", description="Город / место заключения")
    contract_type: str = Field(default="Договор", description="Вид договора (поставка, подряд, аренда, оказание услуг)")
    claim_subject: str = Field(default="", description="Краткое описание предмета договора")
    party_one: PartyInfo = Field(default_factory=PartyInfo, description="Сторона 1 (Заказчик/Покупатель)")
    party_two: PartyInfo = Field(default_factory=PartyInfo, description="Сторона 2 (Исполнитель/Поставщик)")
    finances: ContractFinances = Field(default_factory=ContractFinances, description="Финансовые условия и цена")
    term_start: str = Field(default="", description="Дата начала действия договора / оказания услуг")
    term_end: str = Field(default="", description="Дата окончания действия договора / срок исполнения")
    has_signature_stamp: bool = Field(default=True, description="Наличие подписей и оттисков печатей")

    @field_validator("doc_number", "doc_date", "city", "contract_type", "claim_subject", "term_start", "term_end", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

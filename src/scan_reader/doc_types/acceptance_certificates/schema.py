# -*- coding: utf-8 -*-
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator
from scan_reader.core.finance_parser import parse_russian_currency
from scan_reader.core.fields import ValidatedPartyMixin
from scan_reader.core.utils import coerce_to_str


class CertificateParty(ValidatedPartyMixin):
    name: str = Field(default="", description="Наименование организации или ФИО контрагента")
    address: str = Field(default="", description="Адрес стороны")

    @field_validator("name", "inn", "kpp", "address", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class CertificateFinances(BaseModel):


    amount_no_vat_rub: Optional[float] = Field(ge=0, default=None, description="Стоимость работ/услуг без НДС (руб)")
    vat_rate: str = Field(default="20%", description="Ставка НДС (20%, 10%, 0%, без НДС)")
    vat_amount_rub: Optional[float] = Field(ge=0, default=None, description="Сумма НДС (руб)")
    total_rub: Optional[float] = Field(ge=0, default=None, description="Всего стоимость работ/услуг с НДС (руб)")
    currency: str = Field(default="RUB", description="Валюта документа")

    @field_validator("amount_no_vat_rub", "vat_amount_rub", "total_rub", mode="before")
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)

    @field_validator("vat_rate", "currency", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class AcceptanceCertificateDoc(BaseModel):
    __test__ = False
    doc_number: str = Field(default="", description="Номер акта сдачи-приемки")
    doc_date: str = Field(default="", description="Дата составления акта (DD.MM.YYYY)")
    contract_number: str = Field(default="", description="Номер договора-основания")
    contract_date: str = Field(default="", description="Дата договора-основания (DD.MM.YYYY)")
    contractor: CertificateParty = Field(default_factory=CertificateParty, description="Исполнитель / Подрядчик")
    customer: CertificateParty = Field(default_factory=CertificateParty, description="Заказчик")
    period_start: str = Field(default="", description="Начало отчетного периода оказания услуг")
    period_end: str = Field(default="", description="Окончание отчетного периода")
    claim_subject: str = Field(default="", description="Перечень и содержание выполненных работ / услуг")
    finances: CertificateFinances = Field(default_factory=CertificateFinances, description="Финансовая стоимость и НДС")
    claims_reserved: bool = Field(default=False, description="Наличие претензий у заказчика (false - претензий нет)")
    has_signature_stamp: bool = Field(default=True, description="Наличие подписей и печатей сторон")

    @field_validator("doc_number", "doc_date", "contract_number", "contract_date", "period_start", "period_end", "claim_subject", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

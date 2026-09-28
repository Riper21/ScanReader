# -*- coding: utf-8 -*-
from typing import Any
from pydantic import BaseModel, Field, field_validator
from scan_reader.core.fields import ValidatedPartyMixin
from scan_reader.core.utils import coerce_to_str


class PrincipalInfo(ValidatedPartyMixin):
    name: str = Field(default="", description="Наименование организации-доверителя или ФИО")
    ogrn: str = Field(default="", description="ОГРН / ОГРНИП")
    legal_address: str = Field(default="", description="Юридический адрес")
    signatory_fio: str = Field(default="", description="ФИО лица, выдавшего доверенность")
    signatory_position: str = Field(default="", description="Должность лица (Генеральный директор и др.)")
    signatory_basis: str = Field(default="", description="Основание полномочий (Устав)")

    @field_validator("name", "inn", "kpp", "ogrn", "legal_address", "signatory_fio", "signatory_position", "signatory_basis", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class AttorneyAgentInfo(ValidatedPartyMixin):
    full_name: str = Field(default="", description="ФИО уполномоченного представителя (поверенного)")
    passport_series: str = Field(default="", description="Серия паспорта")
    passport_number: str = Field(default="", description="Номер паспорта")
    passport_issued_by: str = Field(default="", description="Кем выдан паспорт")
    passport_issue_date: str = Field(default="", description="Дата выдачи паспорта")
    snils: str = Field(default="", description="СНИЛС представителя")
    inn: str = Field(default="", description="ИНН представителя")
    registration_address: str = Field(default="", description="Адрес регистрации")

    @field_validator("full_name", "passport_series", "passport_number", "passport_issued_by", "passport_issue_date", "snils", "inn", "registration_address", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class PowerOfAttorneyDoc(BaseModel):


    __test__ = False
    doc_number: str = Field(default="", description="Номер доверенности")
    issue_date: str = Field(default="", description="Дата выдачи доверенности (DD.MM.YYYY)")
    city: str = Field(default="", description="Город / место выдачи")
    valid_until: str = Field(default="", description="Срок действия доверенности (дата или указание срока)")
    principal: PrincipalInfo = Field(default_factory=PrincipalInfo, description="Доверитель")
    agent: AttorneyAgentInfo = Field(default_factory=AttorneyAgentInfo, description="Поверенный / Представитель")
    powers_summary: str = Field(default="", description="Объем передаваемых полномочий")
    can_subdelegate: bool = Field(default=False, description="Право передоверия (true/false)")
    is_notarized: bool = Field(default=False, description="Нотариально удостоверена ли доверенность")
    notary_fio: str = Field(default="", description="ФИО нотариуса (если применимо)")
    has_signature_stamp: bool = Field(default=True, description="Наличие подписи доверителя и печати")

    @field_validator("doc_number", "issue_date", "city", "valid_until", "powers_summary", "notary_fio", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

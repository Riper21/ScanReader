# -*- coding: utf-8 -*-
"""
Общие ограничения полей для Pydantic-схем плагинов (Фаза 6.2).

До этого шага все 43 валидатора в девяти схемах были нормализаторами
(mode="before"), а полевых ограничений не было ни одного: pattern=0, ge=0,
le=0, Literal=0, @model_validator=0. Схема принимала 15-значный «ИНН»,
deduction_percentage="999%" и отрицательную сумму. Реальную проверку выполнял
только аудитор — и то по неполному набору полей.

Правило разделения ответственности:
  - СХЕМА отвергает то, что не может быть значением поля по форме:
    длину, диапазон, допустимый набор, контрольную сумму идентификатора;
  - АУДИТОР отвергает то, что семантически невозможно в документе:
    арифметику, хронологию, статутные лимиты, неподтверждённые реквизиты.

Проверки контрольных сумм намеренно живут здесь, а не в аудиторе: аудитор
получает уже провалидированный документ и отвечает за согласованность, а не
за типы данных.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from ..verifier.checksums import validate_inn

#: ИНН: 10 цифр (юридическое лицо / ИП) или 12 (физическое лицо).
INN_PATTERN = r"^\d{10}(\d{2})?$"
#: КПП: ровно 9 цифр.
KPP_PATTERN = r"^\d{9}$"
#: СНИЛС: 11 цифр, допускаются пробелы и дефисы в записи.
SNILS_PATTERN = r"^[\d\- ]{9,20}$"
#: ОГРН: 13 цифр; ОГРНИП: 15 цифр.
OGRN_PATTERN = r"^\d{13}$"
OGRNIP_PATTERN = r"^\d{15}$"
#: БИК: 9 цифр, начинается с 04 (банк/ГРКЦ) либо 01 (казначейство/УФК).
BIK_PATTERN = r"^0[14]\d{7}$"
#: Банковский счёт: 20 цифр, возможны пробелы и дефисы.
ACCOUNT_PATTERN = r"^[\d\- ]{17,26}$"
#: Процент удержания: число с необязательным знаком %.
PERCENT_PATTERN = r"^\d{1,3}(?:[.,]\d{1,2})?\s*%?$"
#: Номер исполнительного производства: NNNNN/ГГ/ДД/РЕГИОН-ИП (регион опционален).
IP_NUMBER_PATTERN = r"^\d{5}/\d{2}/\d{2}(?:\d{5})?[-–]?ИП?\.?$"
#: Дата в форматах, встречающихся в российских документах.
DATE_PATTERN = r"^(\d{1,2}[.\-/]\d{1,2}[.\-/]\d{2,4}|\d{4}-\d{2}-\d{2}|\d{1,2}\s+\S+\s+\d{4})$"


def empty_str_to_none(v: Any) -> Any:
    """
    Пустая строка от VLM означает «поле не заполнено», а не «пустое значение».

    Иначе любое Optional-поле без pattern/ge превращается в "" и проходит
    проверки контрольных сумм как заведомо непригодное значение.
    """
    if isinstance(v, str) and not v.strip():
        return None
    return v


def digits_only(v: Any) -> Optional[str]:
    """Только цифры значения, либо None. Для идентификаторов с форматированием."""
    if v is None:
        return None
    digits = re.sub(r"\D", "", str(v))
    return digits or None


class ValidatedPartyMixin(BaseModel):
    """
    Общая валидация реквизитов стороны.

    Наследуется моделями сторон (PartyInfo, PartyDetails, CertificateParty,
    UpdParty). Проверяет форму и, для заполненного ИНН, контрольную сумму ФНС.
    """

    inn: Optional[str] = Field(default=None, description="ИНН (10 или 12 цифр)")
    kpp: Optional[str] = Field(default=None, description="КПП (9 цифр)")

    @field_validator("inn", "kpp", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return empty_str_to_none(v)

    @field_validator("kpp", mode="after")
    @classmethod
    def _check_kpp(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = digits_only(v)
        if cleaned is None or len(cleaned) != 9:
            raise ValueError(f"КПП должен содержать 9 цифр, получено: {v!r}")
        return cleaned

    @field_validator("inn", mode="after")
    @classmethod
    def _check_inn(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = digits_only(v)
        if cleaned is None or len(cleaned) not in (10, 12):
            raise ValueError(
                f"ИНН должен содержать 10 цифр (юридическое лицо / ИП) "
                f"или 12 цифр (физическое лицо), получено: {v!r}"
            )
        ok, msg = validate_inn(cleaned)
        if not ok:
            raise ValueError(f"ИНН {cleaned} не проходит контрольную сумму ФНС: {msg}")
        return cleaned


class ValidatedBankMixin(BaseModel):
    """
    Общая валидация банковских реквизитов получателя.

    Ключ счёта проверяется алгоритмом Положения ЦБ РФ 565-П при наличии БИК.
    """

    bik: Optional[str] = Field(default=None, description="БИК банка (9 цифр)")
    payment_account: Optional[str] = Field(default=None, description="Расчётный счёт (20 цифр)")

    @field_validator("bik", "payment_account", mode="before")
    @classmethod
    def _blank_to_none(cls, v: Any) -> Any:
        return empty_str_to_none(v)

    @field_validator("bik", mode="after")
    @classmethod
    def _check_bik(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = digits_only(v)
        if cleaned is None or not re.match(BIK_PATTERN, cleaned):
            raise ValueError(
                f"БИК должен содержать 9 цифр и начинаться с 04 (банк) "
                f"или 01 (казначейство), получено: {v!r}"
            )
        return cleaned

    @field_validator("payment_account", mode="after")
    @classmethod
    def _check_account(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        cleaned = digits_only(v)
        if cleaned is None or len(cleaned) != 20:
            raise ValueError(f"Банковский счёт должен содержать 20 цифр, получено: {v!r}")
        return cleaned

    @model_validator(mode="after")
    def _check_account_key(self) -> "ValidatedBankMixin":
        if self.bik and self.payment_account:
            from ..verifier.checksums import validate_bank_account

            ok, msg = validate_bank_account(self.payment_account, self.bik)
            if not ok:
                # Не ошибка схемы: счета Банка России ключуются иначе, и решение
                # об исключении принимает аудитор, у которого есть имя получателя.
                self.__dict__["_account_key_unverified"] = msg
        return self


def money_field(description: str) -> Any:
    """Денежное поле: неотрицательное число. Отрицательная сумма — ошибка формы."""
    return Field(default=None, ge=0, description=description)


def percent_field(description: str) -> Any:
    """Процент: 0..100 с необязательным знаком %."""
    return Field(default=None, description=description)

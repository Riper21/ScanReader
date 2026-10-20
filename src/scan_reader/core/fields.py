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

from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator

from .utils import get_logger
from ..verifier.checksums import validate_inn

_logger = get_logger("core.fields")

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


#: Б1 (симптом doc_type): заглушки, которыми модель подменяет вид
#: документа, когда не прочитала его. Сравнение после канонизации
#: (is_doc_type_placeholder): нижний регистр, ё→е, удаление небуквенных
#: символов, сжатие пробелов — «N/A», «n/a» и «не указано» дают один ключ.
DOC_TYPE_NONVALUES = frozenset({
    "", "unknown", "undefined", "null", "none", "nan",
    "n a", "n d", "nd", "н а", "н д",
    "не определен", "неопределен", "неопределенный",
    "неопределенный документ", "не определен документ",
    "неопределенный тип документа", "не определен тип документа",
    "неизвестно", "неизвестный", "неизвестный документ",
    "неизвестный тип документа", "не удалось определить",
    "нет", "нет данных", "нет значения", "нет типа",
    "отсутствует", "отсутствует значение",
    "не указан", "не указано", "не заполнено", "не распознан", "не распознано",
    "тип документа", "вид документа", "doc type", "document type",
})


def is_doc_type_placeholder(value: Any) -> bool:
    """
    True, если значение doc_type — заглушка, а не вид документа.

    None — тоже заглушка: поле с None не содержит вида документа, но и не
    должно молча превращаться в пустую строку (Правило 6 о наименьшей
    строгости: ошибка обязана остаться видимой).
    """
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    norm = re.sub(r"[^0-9a-zа-я ]+", " ", value.lower().replace("ё", "е"))
    return " ".join(norm.split()) in DOC_TYPE_NONVALUES


class DocTypeGuardMixin(BaseModel):
    """
    Защита корневого поля doc_type плагина от заглушек.

    Наследуется ТОЛЬКО корневыми моделями плагинов, объявляющими doc_type
    (схема с полем doc_type обязана дать ему осмысленный default). Вложенные
    модели и универсальная схема его не наследуют: пустой вложенный doc_type —
    «поля нет в документе», а не сбой модели.

    Заглушка в корне — признак того, что модель не прочитала документ
    (Правило 1: тихий сбой запрещён), поэтому схема её отвергает, а фасад
    восстанавливает каноническое значение из декларации плагина (Б2).
    """

    # check_fields=False: поле doc_type объявляет СУБКЛАСС (миксин не
    # переобъявляет его, иначе default-декларации плагинов конфликтовали бы
    # с миксином — как с переобъявлением inn/kpp в ValidatedPartyMixin).
    @field_validator("doc_type", mode="after", check_fields=False)
    @classmethod
    def _reject_placeholder_doc_type(cls, v: str) -> str:
        if is_doc_type_placeholder(v):
            raise ValueError(
                f"doc_type — заглушка, а не вид документа: {v!r}"
            )
        return v


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
    #: P2.7: небезопасный ключ счёта — приватный атрибут модели, а не
    #: ручная запись в __dict__ (последняя обходила Pydantic и могла
    #: протекать в сериализацию extras).
    _account_key_unverified: Optional[str] = PrivateAttr(default=None)

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
                # P2.7: маркер сохраняется в PrivateAttr и дублируется
                # предупреждением в лог — раньше несходимость ключа была
                # полностью тихой (Правило 1).
                self._account_key_unverified = msg
                _logger.warning(
                    "Ключ расчётного счёта не сходится с БИК: %s. "
                    "Реквизиты переданы аудитору, решение об исключении — за ним.",
                    msg,
                )
        return self


def percent_field(description: str) -> Any:
    """
    Процент: 0..100 с необязательным знаком %.

    P2.7: раньше docstring обещал диапазон 0..100, но Field не задавал
    границ вовсе, и «999%» проходило схему. Форма значения — на схеме
    (Правило 6), аудитор проверяет лишь статутные лимиты.
    """
    return Field(default=None, ge=0, le=100, description=description)

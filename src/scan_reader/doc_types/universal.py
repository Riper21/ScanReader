# -*- coding: utf-8 -*-
"""
Универсальная (резервная) схема для нераспознанных документов.

P2.1: до этого шага UniversalDocumentDoc и универсальный промпт жили в
facade.py, из-за чего ядро знало лексику полей («claim_subject»,
«total_rub»), а тест-сканер Правила 3 не мог расширить зону проверки на
фасад. Схема перенесена в зону плагинов (doc_types/), как и схемы всех
остальных типов; фасад импортирует её как резерв.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator

from ..core.finance_parser import parse_russian_currency
from ..core.utils import coerce_to_str


class UniversalDocumentDoc(BaseModel):
    """Базовая универсальная Pydantic-схема для неопределенных или неподдерживаемых типов документов."""

    __test__ = False

    file_name: str = Field(default="", description="Имя файла документа")
    doc_type: str = Field(default="Неопределенный документ", description="Тип документа")
    doc_date: str = Field(default="", description="Дата документа")
    doc_number: str = Field(default="", description="Номер документа")
    court_or_authority: str = Field(default="", description="Суд или орган")
    claimant_name: str = Field(default="", description="Взыскатель/истец")
    debtor_name: str = Field(default="", description="Должник/ответчик")
    claim_subject: str = Field(default="", description="Предмет требования")
    total_rub: Optional[float] = Field(default=None, description="Сумма в рублях")
    notes: str = Field(default="Требуется ручная проверка типа документа", description="Примечания")

    @field_validator('total_rub', mode='before')
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)

    @field_validator(
        'file_name', 'doc_type', 'doc_date', 'doc_number', 'court_or_authority',
        'claimant_name', 'debtor_name', 'claim_subject', 'notes',
        mode='before'
    )
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


#: Промпт резервной экстракции для неизвестного типа. Живёт здесь, а не в
#: фасаде, по той же причине, что и схема: это лексика документа, и ядро
#: её знать не обязано (Правило 3 AGENTS.md).
UNIVERSAL_PROMPT_TEXT = (
    "Ты — юридический аналитик. Проанализируй документ и извлеки базовые реквизиты строго в JSON:\n"
    "{\n"
    '  "doc_type": "Неопределенный документ",\n'
    '  "doc_date": "дата",\n'
    '  "doc_number": "номер",\n'
    '  "court_or_authority": "суд или орган",\n'
    '  "claimant_name": "взыскатель/истец",\n'
    '  "debtor_name": "должник/ответчик",\n'
    '  "claim_subject": "предмет требования",\n'
    '  "total_rub": null,\n'
    '  "notes": "Требуется ручная проверка типа документа"\n'
    "}"
)

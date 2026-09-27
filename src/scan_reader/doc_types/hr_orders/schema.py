# -*- coding: utf-8 -*-
from typing import Optional, Any
from pydantic import BaseModel, Field, field_validator
from scan_reader.core.finance_parser import parse_russian_currency
from scan_reader.core.utils import coerce_to_str


class HrEmployeeInfo(BaseModel):
    full_name: str = Field(default="", description="ФИО работника")
    personnel_number: str = Field(default="", description="Табельный номер")
    structural_unit: str = Field(default="", description="Структурное подразделение (отдел, департамент, цех)")
    position: str = Field(default="", description="Должность, специальность или профессия")
    salary_rub: Optional[float] = Field(default=None, description="Оклад / тарифная ставка (руб)")
    bonus_rub: Optional[float] = Field(default=None, description="Надбавка (руб)")
    snils: str = Field(default="", description="СНИЛС работника")
    inn: str = Field(default="", description="ИНН работника")

    @field_validator("full_name", "personnel_number", "structural_unit", "position", "snils", "inn", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

    @field_validator("salary_rub", "bonus_rub", mode="before")
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)


class HrOrderDoc(BaseModel):
    doc_number: str = Field(default="", description="Номер приказа/распоряжения")
    doc_date: str = Field(default="", description="Дата составления приказа (DD.MM.YYYY)")
    form_code: str = Field(default="Т-1", description="Код унифицированной формы (Т-1, Т-5, Т-6, Т-8 или Свободная форма)")
    order_type: str = Field(default="Прием на работу", description="Вид кадрового приказа (Прием, Перевод, Отпуск, Увольнение, Премирование)")
    organization_name: str = Field(default="", description="Наименование организации-работодателя")
    organization_inn: str = Field(default="", description="ИНН организации-работодателя")
    organization_okpo: str = Field(default="", description="Код по ОКПО")
    employee: HrEmployeeInfo = Field(default_factory=HrEmployeeInfo, description="Данные сотрудника")
    effective_date_from: str = Field(default="", description="Дата начала действия приказа / дата приема / начала отпуска")
    effective_date_to: str = Field(default="", description="Дата окончания действия / окончания отпуска")
    probation_period_months: Optional[int] = Field(default=None, description="Испытательный срок (в месяцах)")
    employment_contract_number: str = Field(default="", description="Номер трудового договора")
    employment_contract_date: str = Field(default="", description="Дата трудового договора")
    order_basis: str = Field(default="", description="Основание издания приказа (заявление, докладная записка и др.)")
    director_signatory: str = Field(default="", description="ФИО руководителя организации")
    employee_signature_date: str = Field(default="", description="Дата ознакомления работника с приказом")
    has_signatures: bool = Field(default=True, description="Наличие подписей руководителя и сотрудника")

    @field_validator("doc_number", "doc_date", "form_code", "order_type", "organization_name", "organization_inn", "organization_okpo", "effective_date_from", "effective_date_to", "employment_contract_number", "employment_contract_date", "order_basis", "director_signatory", "employee_signature_date", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

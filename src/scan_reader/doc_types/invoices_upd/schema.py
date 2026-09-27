# -*- coding: utf-8 -*-
from typing import Optional, List, Any
from pydantic import BaseModel, Field, field_validator
from scan_reader.core.finance_parser import parse_russian_currency
from scan_reader.core.utils import coerce_to_str


class UpdParty(BaseModel):
    name: str = Field(default="", description="Наименование организации / ИП")
    inn: str = Field(default="", description="ИНН (10 или 12 цифр)")
    kpp: str = Field(default="", description="КПП организации (9 цифр)")
    address: str = Field(default="", description="Адрес местонахождения")

    @field_validator("name", "inn", "kpp", "address", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class UpdItem(BaseModel):
    row_num: str = Field(default="1", description="Номер строки")
    name: str = Field(default="", description="Наименование товара / описания выполненных работ / услуг")
    unit: str = Field(default="шт", description="Единица измерения (код или условное обозначение)")
    quantity: Optional[float] = Field(default=None, description="Количество (объем)")
    price: Optional[float] = Field(default=None, description="Цена за единицу измерения без НДС")
    amount_no_vat: Optional[float] = Field(default=None, description="Стоимость товаров без налога")
    vat_rate: str = Field(default="20%", description="Налоговая ставка (20%, 10%, 0%, без НДС)")
    vat_amount: Optional[float] = Field(default=None, description="Сумма налога (НДС)")
    total_amount: Optional[float] = Field(default=None, description="Стоимость товаров всего с учетом налога")

    @field_validator("quantity", "price", "amount_no_vat", "vat_amount", "total_amount", mode="before")
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)

    @field_validator("row_num", "name", "unit", "vat_rate", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class UpdFinances(BaseModel):
    total_rub_no_vat: Optional[float] = Field(default=None, description="Всего стоимость без налога")
    total_vat_rub: Optional[float] = Field(default=None, description="Всего сумма налога (НДС)")
    total_rub: Optional[float] = Field(default=None, description="Всего к оплате с учетом налога")
    currency: str = Field(default="Российский рубль, 643", description="Валюта документа")

    @field_validator("total_rub_no_vat", "total_vat_rub", "total_rub", mode="before")
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)

    @field_validator("currency", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)


class InvoiceUpdDoc(BaseModel):
    doc_number: str = Field(default="", description="Номер счета-фактуры / УПД")
    doc_date: str = Field(default="", description="Дата составления документа (DD.MM.YYYY)")
    status: str = Field(default="1", description="Статус документа (1 - счет-фактура и передаточный документ, 2 - только передаточный документ)")
    seller: UpdParty = Field(default_factory=UpdParty, description="Продавец / Исполнитель")
    buyer: UpdParty = Field(default_factory=UpdParty, description="Покупатель / Заказчик")
    shipper: UpdParty = Field(default_factory=UpdParty, description="Грузоотправитель и его адрес")
    consignee: UpdParty = Field(default_factory=UpdParty, description="Грузополучатель и его адрес")
    contract_base: str = Field(default="", description="Основание передачи (договор, заказ, счет)")
    items: List[UpdItem] = Field(default_factory=list, description="Построчные позиции товаров / услуг")
    finances: UpdFinances = Field(default_factory=UpdFinances, description="Итоговые финансовые суммы")
    passed_fio: str = Field(default="", description="Товар (груз) передал / услуги оказал (ФИО, должность)")
    accepted_fio: str = Field(default="", description="Товар (груз) получил / услуги принял (ФИО, должность)")
    has_signature_stamp: bool = Field(default=True, description="Наличие подписей и печатей сторон")

    @field_validator("doc_number", "doc_date", "status", "contract_base", "passed_fio", "accepted_fio", mode="before")
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)

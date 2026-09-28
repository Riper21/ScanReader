# -*- coding: utf-8 -*-
"""
Тесты для новых поддерживаемых типов документов:
1. commercial_contracts (Договоры)
2. invoices_upd (УПД и товарные накладные)
3. acceptance_certificates (Акты приема-передачи)
4. powers_of_attorney (Доверенности)
5. legal_claims (Досудебные претензии)
6. hr_orders (Кадровые приказы)
"""

import pytest
from scan_reader.facade import LegalDocPlatformFacade
from scan_reader.type_registry import get_registry
from scan_reader.doc_types.commercial_contracts.schema import CommercialContractDoc
from scan_reader.doc_types.invoices_upd.schema import InvoiceUpdDoc
from scan_reader.doc_types.acceptance_certificates.schema import AcceptanceCertificateDoc
from scan_reader.doc_types.powers_of_attorney.schema import PowerOfAttorneyDoc
from scan_reader.doc_types.legal_claims.schema import LegalClaimDoc
from scan_reader.doc_types.hr_orders.schema import HrOrderDoc


def test_registry_contains_all_nine_plugins():
    registry = get_registry()
    enabled = registry.enabled()
    expected_ids = {
        "enforcement_orders",
        "executive_documents",
        "salary_deductions",
        "commercial_contracts",
        "invoices_upd",
        "acceptance_certificates",
        "powers_of_attorney",
        "legal_claims",
        "hr_orders",
    }
    assert expected_ids.issubset(set(enabled.keys()))
    assert len(enabled) >= 9


def test_heuristic_classification_for_new_plugins():
    facade = LegalDocPlatformFacade()

    test_cases = [
        ("incoming/commercial_contracts/dogovor_postavki_14.pdf", "commercial_contracts"),
        ("scans/договоры/договор_аренды_2024.pdf", "commercial_contracts"),
        ("incoming/invoices_upd/upd_123_45.pdf", "invoices_upd"),
        ("docs/счет-фактура_и_упд.jpg", "invoices_upd"),
        ("incoming/acceptance_certificates/akt_priemki_rabot_88.jpg", "acceptance_certificates"),
        ("scans/акты/акт сдачи-приемки услуг.pdf", "acceptance_certificates"),
        ("incoming/powers_of_attorney/doverennost_generalnaya.pdf", "powers_of_attorney"),
        ("archive/доверенность_на_представление_интересов.jpg", "powers_of_attorney"),
        ("incoming/legal_claims/dosudebnaya_pretenziya_01.pdf", "legal_claims"),
        ("inbox/претензия_по_договору_поставки.pdf", "legal_claims"),
        ("incoming/hr_orders/prikaz_o_prieme_t1.pdf", "hr_orders"),
        ("personnel/приказ о приеме на работу.docx", "hr_orders"),
    ]

    for path, expected_type in test_cases:
        doc_type, conf, method = facade.classify_document(path)
        assert doc_type == expected_type, f"Path '{path}' expected {expected_type}, got {doc_type}"
        assert conf >= 0.85
        assert method in ("heuristic_path", "heuristic_keyword")


def test_commercial_contract_schema_validation():
    raw_data = {
        "doc_number": "Д-105/24",
        "doc_date": "12.03.2024",
        "city": "г. Санкт-Петербург",
        "contract_type": "Договор поставки",
        "party_one": {
            "name": "ООО «Северный Альянс»",
            "role": "Покупатель",
            "inn": "7802312751",
            "kpp": "780101001",
            "ogrn": "1037800012345",
            "signatory_fio": "Смирнов А.В.",
            "signatory_basis": "Устав"
        },
        "party_two": {
            "name": "АО «ПромПоставка»",
            "role": "Поставщик",
            "inn": "7736207543",
            "kpp": "770501001",
            "ogrn": "1027700543210",
            "signatory_fio": "Кузнецов И.П.",
            "signatory_basis": "Доверенность №4"
        },
        "finances": {
            "total_rub": "1 500 000,50 руб.",
            "vat_included": True,
            "vat_rate": "20%",
            "vat_amount_rub": "250 000,08"
        },
        "term_start": "15.03.2024",
        "term_end": "31.12.2024",
        "has_signature_stamp": True
    }
    doc = CommercialContractDoc(**raw_data)
    assert doc.doc_number == "Д-105/24"
    assert doc.party_one.inn == "7802312751"
    assert doc.finances.total_rub == pytest.approx(1500000.50)
    assert doc.finances.vat_amount_rub == pytest.approx(250000.08)


def test_invoice_upd_schema_validation():
    raw_data = {
        "doc_number": "142",
        "doc_date": "18.05.2024",
        "status": 1,
        "seller": {
            "name": "ООО «Торговый Дом»",
            "inn": "7707083893",
            "kpp": "771101001",
            "address": "г. Москва, ул. Ленина, д. 5"
        },
        "buyer": {
            "name": "ИП Сидоров В.В.",
            "inn": "500100732259",
            "kpp": "",
            "address": "г. Москва, ул. Мира, д. 12"
        },
        "finances": {
            "total_rub_no_vat": "100 000,00",
            "total_vat_rub": "20 000,00",
            "total_rub": "120 000,00"
        },
        "items": [
            {
                "row_num": "1",
                "name": "Кабель силовой ВВГнг",
                "quantity": 500.0,
                "unit": "м",
                "price": "200,00",
                "amount_no_vat": "100 000,00",
                "vat_rate": "20%",
                "vat_amount": "20 000,00",
                "total_amount": "120 000,00"
            }
        ],
        "has_signature_stamp": True
    }
    doc = InvoiceUpdDoc(**raw_data)
    assert doc.doc_number == "142"
    assert doc.finances.total_rub == pytest.approx(120000.00)
    assert len(doc.items) == 1
    assert doc.items[0].quantity == 500.0


def test_acceptance_certificate_schema_validation():
    raw_data = {
        "doc_number": "А-45",
        "doc_date": "30.06.2024",
        "customer": {"name": "ООО «Клиент»", "inn": "7707083893"},
        "contractor": {"name": "ООО «Подрядчик»", "inn": "7736207543"},
        "contract_number": "12-ПР",
        "contract_date": "10.01.2024",
        "finances": {
            "total_rub": "450 000,00",
            "vat_amount_rub": "75 000,00"
        },
        "claim_subject": "Разработка программного обеспечения",
        "claims_reserved": False
    }
    doc = AcceptanceCertificateDoc(**raw_data)
    assert doc.doc_number == "А-45"
    assert doc.finances.total_rub == pytest.approx(450000.00)
    assert doc.claims_reserved is False


def test_power_of_attorney_schema_validation():
    raw_data = {
        "doc_number": "ДОВ-01",
        "issue_date": "01.06.2024",
        "city": "г. Москва",
        "valid_until": "01.06.2025",
        "principal": {
            "name": "ООО «ГлавХолдинг»",
            "inn": "7802312751",
            "signatory_fio": "Сергеев С.С.",
            "signatory_position": "Генеральный директор"
        },
        "agent": {
            "full_name": "Васильев Алексей Михайлович",
            "passport_series": "4512",
            "passport_number": "987654",
            "snils": "111-222-333 44"
        },
        "powers_summary": "Полные полномочия в судах и госорганах",
        "can_subdelegate": False,
        "is_notarized": False
    }
    doc = PowerOfAttorneyDoc(**raw_data)
    assert doc.doc_number == "ДОВ-01"
    assert doc.principal.inn == "7802312751"
    assert doc.agent.passport_series == "4512"
    assert doc.can_subdelegate is False


def test_legal_claim_schema_validation():
    raw_data = {
        "doc_number": "ПРЕТ-2024/7",
        "doc_date": "15.07.2024",
        "sender": {"name": "ООО «Взыскатель»", "inn": "7707083893"},
        "recipient": {"name": "ООО «Должник»", "inn": "7736207543"},
        "contract_basis_number": "ДОГ-88",
        "contract_basis_date": "01.02.2024",
        "finances": {
            "principal_debt_rub": "800 000,00",
            "penalty_rub": "40 000,00",
            "total_claim_rub": "840 000,00"
        },
        "response_deadline_days": 10
    }
    doc = LegalClaimDoc(**raw_data)
    assert doc.doc_number == "ПРЕТ-2024/7"
    assert doc.finances.principal_debt_rub == pytest.approx(800000.00)
    assert doc.finances.total_claim_rub == pytest.approx(840000.00)
    assert doc.response_deadline_days == 10


def test_hr_order_schema_validation():
    raw_data = {
        "doc_number": "12-к",
        "doc_date": "01.08.2024",
        "form_code": "Т-1",
        "order_type": "Прием на работу",
        "organization_name": "ООО «ТехСтрой»",
        "organization_inn": "7802312751",
        "employee": {
            "full_name": "Николаев Денис Юрьевич",
            "personnel_number": "00452",
            "structural_unit": "Отдел разработки",
            "position": "Ведущий инженер-программист",
            "salary_rub": "180 000,00 руб."
        },
        "effective_date_from": "05.08.2024",
        "probation_period_months": 3,
        "employment_contract_number": "ТД-452",
        "employment_contract_date": "01.08.2024",
        "has_signatures": True
    }
    doc = HrOrderDoc(**raw_data)
    assert doc.doc_number == "12-к"
    assert doc.form_code == "Т-1"
    assert doc.employee.salary_rub == pytest.approx(180000.00)
    assert doc.employee.position == "Ведущий инженер-программист"

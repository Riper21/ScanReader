# -*- coding: utf-8 -*-
import os
import json

from scan_reader.core.json_exporter import (
    save_single_document_json,
    export_consolidated_registries,
    save_checkpoint,
    load_checkpoint,
    clean_checkpoint
)
from scan_reader.core.metrics_evaluator import (
    evaluate_dataset,
    generate_run_summary,
    export_metrics_json,
    append_to_metrics_history,
    export_run_summary_markdown,
    validate_inn_string,
    validate_case_number_format,
    validate_date_string,
    validate_math_balance,
    validate_deduction_rate
)


def test_validator_helpers():
    # ИНН
    valid_inn10 = "7707083893"  # Сбербанк
    is_v, inn, score = validate_inn_string(f"ИНН {valid_inn10}, КПП 773601001")
    assert is_v is True
    assert inn == valid_inn10
    assert score == 100.0

    # Номер дела
    is_case, c_score = validate_case_number_format("А40-12345/2021")
    assert is_case is True
    assert c_score == 100.0

    # Дата
    is_d, d_score = validate_date_string("15.04.2023")
    assert is_d is True
    assert d_score == 100.0

    # Баланс
    is_bal, diff, b_score = validate_math_balance(100.0, 10.0, 5.0, 0.0, 115.0)
    assert is_bal is True
    assert diff == 0.0
    assert b_score == 100.0

    # Процент удержания
    is_r, r_score = validate_deduction_rate("50% ежемесячно")
    assert is_r is True
    assert r_score == 100.0


def test_json_exporter_single_and_checkpoint(tmp_path):
    out_dir = str(tmp_path / "results")
    doc_res = {
        "file_name": "scan_01.pdf",
        "doc_type": "executive_documents",
        "data": {
            "case_number": "А40-111/2022",
            "finances": {"total_rub": 50000.0}
        },
        "quality_score_percent": 98.5,
        "quality_status": "excellent",
        "status": "COMPLETED"
    }

    # Сохранение единичного документа
    f_path = save_single_document_json(doc_res, out_dir)
    assert os.path.exists(f_path)
    with open(f_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded["file_name"] == "scan_01.pdf"
    assert loaded["quality_score_percent"] == 98.5

    # Проверка Full и Flat файлов
    full_file = os.path.join(out_dir, "scan_01_Full.json")
    flat_file = os.path.join(out_dir, "scan_01_Flat.json")
    assert os.path.exists(full_file), "scan_01_Full.json must be created"
    assert os.path.exists(flat_file), "scan_01_Flat.json must be created"

    with open(full_file, "r", encoding="utf-8") as f:
        full_data = json.load(f)
    assert full_data["file_name"] == "scan_01.pdf"
    assert "data" in full_data

    with open(flat_file, "r", encoding="utf-8") as f:
        flat_data = json.load(f)
    assert flat_data["FileName"] == "scan_01.pdf"
    assert flat_data["DocType"] == "executive_documents"
    assert flat_data["TotalAmountRub"] == 50000.0
    assert flat_data["QualityScore"] == 98.5
    # Проверка отсутствия вложенных словарей в Flat JSON
    for val in flat_data.values():
        assert not isinstance(val, dict), f"Flat JSON must not contain nested dicts, found: {val}"

    # Чекпоинтинг
    chk_path = os.path.join(out_dir, ".checkpoint_test.json")
    save_checkpoint([doc_res], chk_path)
    assert os.path.exists(chk_path)
    res_chk = load_checkpoint(chk_path)
    assert len(res_chk) == 1
    assert res_chk[0]["file_name"] == "scan_01.pdf"
    clean_checkpoint(chk_path)
    assert not os.path.exists(chk_path)


def test_consolidated_registries_export(tmp_path):
    out_dir = str(tmp_path / "results_cons")
    docs = [
        {
            "file_name": "exec_1.pdf",
            "doc_type": "executive_documents",
            "data": {"case_number": "А40-1/2021", "finances": {"total_rub": 1000.0}},
            "quality_score_percent": 95.0,
            "status": "COMPLETED"
        },
        {
            "file_name": "salary_1.pdf",
            "doc_type": "salary_deductions",
            "data": {"doc_number": "123-ИП", "finances": {"total_deduction_rub": 2000.0}},
            "quality_score_percent": 90.0,
            "status": "COMPLETED"
        }
    ]

    saved = export_consolidated_registries(docs, out_dir)
    assert "documents_registry.json" in saved
    assert "salary_deductions_registry.json" in saved
    assert "all_documents_registry.json" in saved
    assert "Registry_Full.json" in saved
    assert "Registry_Flat.json" in saved

    # Проверка содержимого all_documents_registry.json
    all_path = saved["all_documents_registry.json"]
    with open(all_path, "r", encoding="utf-8") as f:
        all_docs = json.load(f)
    assert len(all_docs) == 2
    assert all_docs[0]["file_name"] == "exec_1.pdf"
    assert all_docs[1]["file_name"] == "salary_1.pdf"

    # Проверка содержимого Registry_Full.json и Registry_Flat.json
    with open(saved["Registry_Full.json"], "r", encoding="utf-8") as f:
        full_reg = json.load(f)
    assert len(full_reg) == 2

    with open(saved["Registry_Flat.json"], "r", encoding="utf-8") as f:
        flat_reg = json.load(f)
    assert len(flat_reg) == 2
    # Все элементы flat_reg должны быть плоскими (без словарей внутри)
    for entry in flat_reg:
        for val in entry.values():
            assert not isinstance(val, dict)


def test_metrics_evaluator_autonomous_and_summary(tmp_path):
    docs = [
        {
            "file_name": "order_1.pdf",
            "doc_type": "enforcement_orders",
            "doc_date": "10.01.2023",
            "ip_number": "12345/23/01/77",
            "fssp": {"name": "Кировский РОСП УФССП России", "officer": "Пристав Иванов"},
            "court": {"name": "Мировой судья №1", "case_number": "2-123/2022"},
            "claimant": {"name": "Банк ВТБ", "details": "ИНН 7702070139"},
            "debtor": {"name": "Иванов И.И.", "details": "ИНН 771234567801"},
            "claim_subject": "Взыскание задолженности по кредиту",
            "finances": {"main_debt_rub": 10000.0, "total_rub": 10000.0}
        }
    ]

    res = evaluate_dataset(docs, ground_truth_docs=None, doc_type="enforcement_orders")
    assert res["total_documents"] == 1
    assert res["mode"] == "autonomous"
    assert res["average_quality_score_percent"] >= 85.0
    assert res["status_counts"]["excellent"] + res["status_counts"]["high"] == 1

    # Run summary
    summary = generate_run_summary({"enforcement_orders": res})
    assert summary["total_documents_processed"] == 1
    assert summary["overall_quality_score_percent"] >= 80.0

    # Экспорт JSON и MD
    j_path = str(tmp_path / "run_summary.json")
    m_path = str(tmp_path / "run_summary.md")
    h_path = str(tmp_path / "history.json")

    export_metrics_json(summary, j_path)
    assert os.path.exists(j_path)
    export_run_summary_markdown(summary, m_path)
    assert os.path.exists(m_path)
    append_to_metrics_history(summary, h_path)
    assert os.path.exists(h_path)
    with open(h_path, "r", encoding="utf-8") as f:
        h_data = json.load(f)
    assert len(h_data) == 1


def test_launcher_symbols_and_sanitize_filename():
    """Проверка доступности sanitize_filename в модуле launcher."""
    import scan_reader.launcher as launcher
    from pathlib import Path
    assert hasattr(launcher, "sanitize_filename")
    clean = launcher.sanitize_filename(Path("test/doc:name?.pdf").stem)
    assert ":" not in clean
    assert "?" not in clean


def test_salary_1c_target_conversion_and_export(tmp_path):
    """Проверка полного соответствия формируемого JSON эталону 1С из скриншота."""
    from scan_reader.core.json_exporter import (
        convert_salary_to_target_1c,
        save_single_document_json,
        export_consolidated_registries
    )

    sample = {
        "file_name": "nmc5o05g.pdf",
        "doc_type": "salary_deductions",
        "data": {
            "doc_date": "28.08.2026",
            "doc_number": "610077/26/66050-ИП",
            "ip_number": "610077/26/66050-ИП",
            "base_doc": "Судебный приказ № 2-967 от 21.06.2024",
            "base_doc_number": "2-967",
            "base_doc_date": "21.06.2024",
            "authority": {
                "name": "Серовское РОСП ГУФССП России по Свердловской области",
                "address": "624981, Россия, Свердловская обл., г. Серов, ул. Народная, д. 13",
                "officer": "Смирнова Елена Вячеславовна",
            },
            "employer": {
                "name": 'АКЦИОНЕРНОЕ ОБЩЕСТВО "ПРОИЗВОДСТВЕННАЯ ФИРМА "ПРИМЕР"'
            },
            "claim_subject": "Алименты на содержание детей (***)",
            "payment_details": {
                "bik": "015004950",
                "payment_account": "03212643000000015113",
                "recipient": "УФК по Новосибирской области (Серовское районное отделение судебных приставов ГУ...)",
                "recipient_inn": "6670073012",
                "recipient_kpp": "663232001",
                "oktmo": "65756000",
                "uin": "32266050260610077000",
                "rosp_code": "66050"
            }
        }
    }

    res_1c = convert_salary_to_target_1c(sample)

    expected_keys = [
        "Bik", "DbCode", "ExecutiveDocumentDate", "ExecutiveDocumentNumber",
        "FSSP_Head", "Oktmo", "OrganizationName", "PaymentAccount",
        "PayType", "PurposeType", "Recipient", "RecipientInn",
        "RecipientKpp", "RecipientType", "ResolutionDate", "RospAddressFakt",
        "RospBailiffFio", "RospFullCode", "Uin",
        # Признаки верификации обязаны присутствовать в записи для 1С:
        # бухгалтер должен видеть, проверялся ли документ и что с ним делать.
        "ZeroTrustStatus", "ZeroTrustValid", "RequiresHumanReview", "AdvisoryReview",
    ]
    assert list(res_1c.keys()) == expected_keys
    # Запись без отчёта о верификации: статус пуст, а не «unknown» с valid=True
    assert res_1c["ZeroTrustStatus"] == ""
    assert res_1c["ZeroTrustValid"] is None
    assert res_1c["RequiresHumanReview"] is False
    assert res_1c["Bik"] == "015004950"
    assert res_1c["DbCode"] == 10
    assert res_1c["ExecutiveDocumentDate"] == "2024-06-21"
    assert res_1c["ExecutiveDocumentNumber"] == "2-967"
    assert res_1c["FSSP_Head"] == "Серовское РОСП ГУФССП России по Свердловской области"
    assert res_1c["Oktmo"] == "65756000"
    assert res_1c["OrganizationName"] == 'АКЦИОНЕРНОЕ ОБЩЕСТВО "ПРОИЗВОДСТВЕННАЯ ФИРМА "ПРИМЕР"'
    assert res_1c["PaymentAccount"] == "03212643000000015113"
    assert res_1c["PayType"] == "Aliment"
    assert res_1c["PurposeType"] == "ExecutiveProduction"
    assert res_1c["RecipientInn"] == "6670073012"
    assert res_1c["RecipientKpp"] == "663232001"
    assert res_1c["RecipientType"] == "Budget"
    assert res_1c["ResolutionDate"] == "2026-08-28"
    assert res_1c["RospAddressFakt"] == "624981, Россия, Свердловская обл., г. Серов, ул. Народная, д. 13"
    assert res_1c["RospBailiffFio"] == "Смирнова Елена Вячеславовна"
    assert res_1c["RospFullCode"] == "66050"
    assert res_1c["Uin"] == "32266050260610077000"

    # Проверка сохранения одиночного документа
    out_dir = str(tmp_path / "results_1c_test")
    doc_path = save_single_document_json(sample, out_dir)
    assert os.path.exists(doc_path)
    with open(doc_path, "r", encoding="utf-8") as f:
        saved_data = json.load(f)
    assert list(saved_data.keys()) == expected_keys
    assert saved_data["Bik"] == "015004950"

    # Проверка raw и 1C_Импорт
    raw_path = os.path.join(out_dir, "nmc5o05g_raw.json")
    import_path = os.path.join(out_dir, "1C_Импорт", "nmc5o05g_1c.json")
    assert os.path.exists(raw_path)
    assert os.path.exists(import_path)

    # Проверка консолидированного реестра
    saved_regs = export_consolidated_registries([sample], out_dir)
    assert "salary_deductions_registry_1c.json" in saved_regs
    reg_path = saved_regs["salary_deductions_registry_1c.json"]
    with open(reg_path, "r", encoding="utf-8") as rf:
        reg_data = json.load(rf)
    assert len(reg_data) == 1
    assert reg_data[0]["Bik"] == "015004950"


def test_salary_1c_dates_not_duplicated_and_fallback_regex():
    """Тест исключения дублирования дат и работы фолбэка парсинга основания."""
    from scan_reader.core.json_exporter import convert_salary_to_target_1c

    # Случай 1: Наличие только текстовой строки base_doc (фолбэк парсинг даты и номера)
    doc_with_raw_base = {
        "doc_type": "salary_deductions",
        "data": {
            "doc_date": "21.04.2016",
            "doc_number": "10701/16/30001-ИП",
            "ip_number": "10701/16/30001-ИП",
            "base_doc": "Исполнительный лист № ФС00001234 от 20.01.2016"
        }
    }
    res = convert_salary_to_target_1c(doc_with_raw_base)
    assert res["ResolutionDate"] == "2016-04-21"
    assert res["ExecutiveDocumentDate"] == "2016-01-20"
    assert res["ExecutiveDocumentNumber"] == "ФС00001234"
    assert res["ResolutionDate"] != res["ExecutiveDocumentDate"]

    # Случай 2: base_doc отсутствует вовсе — ExecutiveDocumentDate не должна дублировать ResolutionDate
    doc_without_base = {
        "doc_type": "salary_deductions",
        "data": {
            "doc_date": "15.09.2020",
            "doc_number": "25013/20/428706",
            "ip_number": "25013/20/428706"
        }
    }
    res_no_base = convert_salary_to_target_1c(doc_without_base)
    assert res_no_base["ResolutionDate"] == "2020-09-15"
    assert res_no_base["ExecutiveDocumentDate"] == ""
    assert res_no_base["ExecutiveDocumentDate"] != res_no_base["ResolutionDate"]


def test_full_and_flat_dual_json_export(tmp_path):
    """Тест формирования двух канонических форматов JSON: Full и Flat."""
    from scan_reader.core.json_exporter import convert_to_flat_1c, save_single_document_json

    out_dir = str(tmp_path / "dual_export")

    doc = {
        "file_name": "executive_order_42.pdf",
        "doc_type": "enforcement_orders",
        "data": {
            "doc_date": "15.03.2024",
            "doc_number": "42/2024",
            "fssp": {"name": "УФССП по г. Москве", "officer": "Сидоров С.С."},
            "claimant": {"name": "ПАО Сбербанк", "inn": "7707083893"},
            "debtor": {"name": "Петров П.П.", "inn": "771234567890"},
            "claim_subject": "Кредитная задолженность",
            "finances": {"main_debt_rub": 150000.0, "court_fee_rub": 4500.0, "total_rub": 154500.0}
        },
        "zero_trust": {"status": "zero_trust_verified", "is_valid": True},
        "quality_score_percent": 99.0,
        "processed_at": "2026-09-26 15:00:00"
    }

    # 1. Проверка функции convert_to_flat_1c
    flat_data = convert_to_flat_1c(doc)
    assert flat_data["FileName"] == "executive_order_42.pdf"
    assert flat_data["DocType"] == "enforcement_orders"
    assert flat_data["DocDate"] == "2024-03-15"
    assert flat_data["DocNumber"] == "42/2024"
    assert flat_data["CourtOrAuthority"] == "УФССП по г. Москве"
    assert flat_data["ClaimantName"] == "ПАО Сбербанк"
    assert flat_data["ClaimantInn"] == "7707083893"
    assert flat_data["DebtorName"] == "Петров П.П."
    assert flat_data["DebtAmountRub"] == 150000.0
    assert flat_data["CourtFeeRub"] == 4500.0
    assert flat_data["TotalAmountRub"] == 154500.0
    assert flat_data["ZeroTrustStatus"] == "zero_trust_verified"
    assert flat_data["ZeroTrustValid"] is True
    assert flat_data["QualityScore"] == 99.0

    # Проверка, что ни одно поле не является словарём (100% Flat)
    for k, v in flat_data.items():
        assert not isinstance(v, dict), f"Поле {k} не должно быть словарем в Flat JSON"

    # 2. Проверка сохранения через save_single_document_json
    save_single_document_json(doc, out_dir)

    full_path = os.path.join(out_dir, "executive_order_42_Full.json")
    flat_path = os.path.join(out_dir, "executive_order_42_Flat.json")

    assert os.path.exists(full_path), "executive_order_42_Full.json должен существовать"
    assert os.path.exists(flat_path), "executive_order_42_Flat.json должен существовать"

    with open(full_path, "r", encoding="utf-8") as f_full:
        loaded_full = json.load(f_full)
    assert loaded_full["file_name"] == "executive_order_42.pdf"
    assert loaded_full["data"]["fssp"]["name"] == "УФССП по г. Москве"

    with open(flat_path, "r", encoding="utf-8") as f_flat:
        loaded_flat = json.load(f_flat)
    assert loaded_flat["FileName"] == "executive_order_42.pdf"
    assert loaded_flat["CourtOrAuthority"] == "УФССП по г. Москве"
    assert loaded_flat["TotalAmountRub"] == 154500.0




# -*- coding: utf-8 -*-
"""
Тесты классификации транслитерированных имен, VLM-классификатора и Graceful Degradation.
"""

from scan_reader.facade import LegalDocPlatformFacade, UNKNOWN_CATEGORY


def test_transliterated_filenames_classification():
    facade = LegalDocPlatformFacade()

    # Файл пользователя из скриншота
    dt1, conf1, m1 = facade.classify_document("uderjanie_04.xmyc99xggqjn.jpg", use_vlm_fallback=False)
    assert dt1 == "salary_deductions"
    assert conf1 >= 0.9

    # Исполнительный лист в транслите
    dt2, conf2, m2 = facade.classify_document("ispolnitelny_list_2020.png", use_vlm_fallback=False)
    assert dt2 == "executive_documents"
    assert conf2 >= 0.9

    # Приказ в транслите
    dt3, conf3, m3 = facade.classify_document("prikaz_o_vozbujdenii_ip.jpg", use_vlm_fallback=False)
    assert dt3 == "enforcement_orders"
    assert conf3 >= 0.9


def test_unknown_doc_type_graceful_handling():
    facade = LegalDocPlatformFacade()

    # Проверка, что при неизвестном типе валидация и фасад НЕ падают с KeyError
    val_res = facade.validate_document({"raw_text": "произвольный текст"}, doc_type=UNKNOWN_CATEGORY)
    assert val_res["passed"] is False
    assert val_res["score"] == 0.0
    assert len(val_res["issues"]) > 0


def test_benchmark_against_ground_truth_for_all_registered_types():
    facade = LegalDocPlatformFacade()
    types = facade.registry.ids()
    assert len(types) >= 3
    for t in types:
        bench_res = facade.benchmark_against_ground_truth({}, {}, doc_type=t)
        assert "accuracy" in bench_res
        assert "details" in bench_res

# -*- coding: utf-8 -*-
"""
Тесты классификации транслитерированных имен, VLM-классификатора и Graceful Degradation.
"""

from unittest.mock import MagicMock

import pytest

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


# =========================================================================
# P2.5: сужение текстовых маркеров и подтверждение гипотез роутером
# =========================================================================
_MARKER_POSITIVE = [
    # (нейтральное имя файла, текст документа, ожидаемый тип)
    ("doc_001.txt",
     "ИСПОЛНИТЕЛЬНЫЙ ЛИСТ. Дело № 2-1234/2024. Взыскать с должника в пользу взыскателя.",
     "executive_documents"),
    ("doc_002.txt",
     "Серия ФС №09665763, номер исполнительного производства 42/24/77000-ИП.",
     "executive_documents"),
    ("doc_003.txt",
     "ПОСТАНОВЛЕНИЕ об обращении взыскания на заработную плату должника.",
     "salary_deductions"),
    ("doc_004.txt",
     "Удерживать из заработной платы должника ежемесячно до погашения долга.",
     "salary_deductions"),
    ("doc_005.txt",
     "ПОСТАНОВЛЕНИЕ о возбуждении исполнительного производства от 12.03.2024.",
     "enforcement_orders"),
    ("doc_006.txt",
     "Начальнику отдела — старшему судебному приставу. Судебный пристав-исполнитель.",
     "enforcement_orders"),
    ("doc_007.txt",
     "ПРИКАЗ о приеме на работу. Табельный номер 4218.",
     "hr_orders"),
    ("doc_008.txt",
     "Унифицированная форма № Т-1. Утверждена постановлением Госкомстата.",
     "hr_orders"),
    ("doc_009.txt",
     "ДОСУДЕБНАЯ ПРЕТЕНЗИЯ. Требование об оплате задолженности по договору.",
     "legal_claims"),
    ("doc_010.txt",
     "Расчет процентов по ст. 395 ГК РФ. Срок для добровольного исполнения 10 дней.",
     "legal_claims"),
    ("doc_011.txt",
     "Настоящей доверенностью уполномочиваю представлять интересы в суде.",
     "powers_of_attorney"),
    ("doc_012.txt",
     "Доверенность выдана сроком на три года без права передоверия.",
     "powers_of_attorney"),
    ("doc_013.txt",
     "Универсальный передаточный документ. Счет-фактура № 12 от 03.04.2024.",
     "invoices_upd"),
    ("doc_014.txt",
     "Товарная накладная. Форма по ОКУД 0330012. Грузополучатель: ООО «Вектор».",
     "invoices_upd"),
    ("doc_015.txt",
     "ДОГОВОР № 14/23-П. Предмет договора: поставка оборудования.",
     "commercial_contracts"),
    ("doc_016.txt",
     "АКТ выполненных работ. Претензий по объему, качеству и срокам не имеют.",
     "acceptance_certificates"),
]

# Слова, которые раньше матчились как маркеры чужих типов (должник, оклад,
# претензия...) — теперь без сильного контекста документ не маршрутизируется.
_MARKER_NEGATIVE = [
    ("doc_101.txt", "Должник и взыскатель присутствовали на заседании."),
    ("doc_102.txt", "Удержание налогов производится бухгалтерией организации."),
    ("doc_103.txt", "Оклад согласован в штатном расписании."),
    ("doc_104.txt", "Претензия отклонена, пени не начислены."),
    ("doc_105.txt", "Покупатель осмотрел товар на складе продавца."),
    ("doc_106.txt", "Полномочия органа определены уставом."),
]


@pytest.mark.parametrize("filename,text,expected", _MARKER_POSITIVE)
def test_text_markers_route_known_types(tmp_path, filename, text, expected):
    """P2.5: сильные маркеры маршрутизируют текстовые документы верно."""
    f = tmp_path / filename
    f.write_text(text, encoding="utf-8")
    facade = LegalDocPlatformFacade()
    doc_type, conf, method = facade.classify_document(str(f), use_vlm_fallback=False)
    assert doc_type == expected, f"ожидался {expected}, получен {doc_type} ({method})"
    assert method == "text_content_marker"
    assert conf == 0.92


@pytest.mark.parametrize("filename,text", _MARKER_NEGATIVE)
def test_generic_words_no_longer_misroute(tmp_path, filename, text):
    """P2.5: одиночные генеричные слова (должник, оклад, претензия...) больше
    не перехватывают документ чужим типом."""
    f = tmp_path / filename
    f.write_text(text, encoding="utf-8")
    facade = LegalDocPlatformFacade()
    doc_type, _conf, _method = facade.classify_document(str(f), use_vlm_fallback=False)
    assert doc_type == UNKNOWN_CATEGORY


def test_text_hypothesis_below_095_confirmed_by_router(tmp_path, monkeypatch):
    """P2.5: гипотеза текстового маркера (0.92 < 0.95) не финальна — её
    подтверждает VLM-роутер, и он может опровергнуть её."""
    f = tmp_path / "doc_020.txt"
    f.write_text(
        "Постановление об обращении взыскания на заработную плату должника.",
        encoding="utf-8",
    )
    facade = LegalDocPlatformFacade()

    reply = MagicMock()
    reply.choices = [MagicMock()]
    reply.choices[0].message.content = (
        '{"doc_type": "enforcement_orders", "confidence": 0.93, "reason": "это постановление ФССП"}'
    )
    reply.usage = None
    client = MagicMock()
    client.chat.completions.create.return_value = reply
    monkeypatch.setattr(facade, "_get_client", lambda: client)

    doc_type, conf, method = facade.classify_document(str(f))
    assert doc_type == "enforcement_orders"
    assert method == "vlm_dual_zone_router"


def test_text_hypothesis_survives_router_failure(tmp_path, monkeypatch):
    """P2.5: VLM недоступен — гипотеза маркера остаётся рабочим результатом."""
    f = tmp_path / "doc_021.txt"
    f.write_text(
        "Постановление об обращении взыскания на заработную плату должника.",
        encoding="utf-8",
    )
    facade = LegalDocPlatformFacade()
    monkeypatch.setattr(facade, "_get_client", lambda: None)

    doc_type, conf, method = facade.classify_document(str(f))
    assert doc_type == "salary_deductions"
    assert method == "text_content_marker"
    assert conf == 0.92


def test_path_pattern_095_is_final_skips_router(tmp_path, monkeypatch):
    """P2.5: совпадение с шаблоном пути (0.95) финально — роутер не зовётся."""
    f = tmp_path / "uderjanie_04.jpg"
    f.write_bytes(b"\xff\xd8\xff\xe0dummy")
    facade = LegalDocPlatformFacade()
    monkeypatch.setattr(
        facade, "classify_via_vlm",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("роутер не должен вызываться")),
    )
    doc_type, conf, method = facade.classify_document(str(f), use_vlm_fallback=True)
    assert doc_type == "salary_deductions"
    assert method == "heuristic_path"
    assert conf == 0.95

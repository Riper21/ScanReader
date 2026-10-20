# -*- coding: utf-8 -*-
"""
Б1-Б4 (симптом doc_type): защита вида документа от деградации модели.

Инцидент: модель подменили без уведомления; маршрутизация осталась верной
(шапка результата корректна), но в теле data.doc_type встала заглушка
«unknown», и ни один слой её не заметил. Пакет барьеров:
  Б1 — схема плагина отвергает заглушки doc_type (Правило 6: заглушка не
       может быть значением поля);
  Б2 — фасад восстанавливает каноническое значение из декларации плагина
       (default поля doc_type схемы) и поднимает ВИДИМЫЙ WARNING с маркером
       _doc_type_normalized, а не заменяет молча;
  Б3 — плоская запись 1С берёт тип из шапки результата, а не из тела;
  Б4 — слабое мнение VLM-роутера (conf < VLM_ROUTER_OVERRIDE_MIN) не
       перекрывает уверенную эвристику (>= 0.90).
"""

import json
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from scan_reader.core.fields import is_doc_type_placeholder
from scan_reader.doc_types.enforcement_orders.schema import EnforcementOrderDoc
from scan_reader.doc_types.executive_documents.schema import ExecutiveDocumentDoc
from scan_reader.doc_types.salary_deductions.schema import SalaryDeductionDoc
from scan_reader.doc_types.universal import UniversalDocumentDoc
from scan_reader.facade import LegalDocPlatformFacade, _plugin_doc_type_default
from scan_reader.type_registry import UNKNOWN_CATEGORY, get_registry


# =========================================================================
# Б1: детекция заглушек
# =========================================================================
@pytest.mark.parametrize(
    "value",
    [
        "unknown", "UNKNOWN", "Undefined", "null", "None", "N/A", "n/a", "н/д",
        "не определен", "Не определён", "НЕИЗВЕСТНО", "нет данных",
        "", "  ", "-", "—", "тип документа", "вид документа", None,
    ],
)
def test_b1_placeholders_detected(value):
    """Заглушки распознаются в любом регистре, раскладке и с ё/е."""
    assert is_doc_type_placeholder(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "Исполнительный лист",
        "Заявление взыскателя о возбуждении ИП",
        "Постановление об обращении взыскания на заработную плату и иные доходы должника",
        "Судебный приказ",
        123,
    ],
)
def test_b1_real_doc_types_not_flagged(value):
    """Реальные виды документов (и нестроковые значения) заглушками не считаются."""
    assert is_doc_type_placeholder(value) is False


# =========================================================================
# Б1: корневые схемы плагинов отвергают заглушки
# =========================================================================
_ROOT_SCHEMAS = [
    (EnforcementOrderDoc, "Заявление взыскателя о возбуждении ИП"),
    (ExecutiveDocumentDoc, "Исполнительный лист"),
    (SalaryDeductionDoc, "Постановление об обращении взыскания на заработную плату и иные доходы должника"),
]


@pytest.mark.parametrize("schema_cls,legit", _ROOT_SCHEMAS)
@pytest.mark.parametrize("placeholder", ["unknown", "", "не определен"])
def test_b1_root_schema_rejects_placeholder(schema_cls, legit, placeholder):
    """Схема поднимает ошибку на заглушку — молчаливый пропуск запрещён."""
    with pytest.raises(ValidationError):
        schema_cls.model_validate({"doc_type": placeholder})


@pytest.mark.parametrize("schema_cls,legit", _ROOT_SCHEMAS)
def test_b1_root_schema_accepts_real_value(schema_cls, legit):
    """Декларированный вид документа проходит защиту."""
    assert schema_cls.model_validate({"doc_type": legit}).doc_type == legit


def test_b1_universal_schema_has_no_guard():
    """Универсальная схема — осознанное исключение: там «unknown» честен,
    потому что и шапка результата unknown (маршрут не определён)."""
    assert UniversalDocumentDoc.model_validate({"doc_type": "unknown"}).doc_type == "unknown"


# =========================================================================
# Б2: нормализация в фасаде + видимый WARNING
# =========================================================================
def _mock_client(reply_obj: str) -> MagicMock:
    reply = MagicMock()
    reply.choices = [MagicMock()]
    reply.choices[0].message.content = reply_obj
    reply.usage = None
    client = MagicMock()
    client.chat.completions.create.return_value = reply
    return client


def test_b2_facade_normalizes_placeholder_doc_type(tmp_path, monkeypatch):
    """Заглушка в теле ответа заменяется каноническим значением плагина,
    исходное значение сохраняется в маркере _doc_type_normalized."""
    doc = tmp_path / "ispolnitelny_list.txt"
    doc.write_text("ИСПОЛНИТЕЛЬНЫЙ ЛИСТ. Дело № 2-1234/2020.", encoding="utf-8")

    facade = LegalDocPlatformFacade()
    monkeypatch.setattr(
        facade, "_get_client",
        lambda: _mock_client(json.dumps({"doc_type": "unknown", "case_number": "2-1234/2020"})),
    )

    extracted = facade.extract_document_data(str(doc), doc_type="executive_documents")

    assert extracted["doc_type"] == "Исполнительный лист"
    assert extracted["_doc_type_normalized"] == "unknown"
    assert extracted.get("schema_validated") is not False


def test_b2_absent_doc_type_is_flagged_too(tmp_path, monkeypatch):
    """Отсутствие doc_type в ответе — то же нарушение контракта, что и
    заглушка: ранее поле молча заполнялось default схемы (тихий сбой)."""
    doc = tmp_path / "ispolnitelny_list.txt"
    doc.write_text("ИСПОЛНИТЕЛЬНЫЙ ЛИСТ. Дело № 2-1234/2020.", encoding="utf-8")

    facade = LegalDocPlatformFacade()
    monkeypatch.setattr(
        facade, "_get_client",
        lambda: _mock_client(json.dumps({"case_number": "2-1234/2020"})),
    )

    extracted = facade.extract_document_data(str(doc), doc_type="executive_documents")
    assert extracted["doc_type"] == "Исполнительный лист"
    assert extracted["_doc_type_normalized"] == "None"


def test_b2_real_doc_type_not_touched(tmp_path, monkeypatch):
    """Реальное значение из ответа модели сохраняется дословно."""
    doc = tmp_path / "order.txt"
    doc.write_text("ПОСТАНОВЛЕНИЕ о возбуждении исполнительного производства.", encoding="utf-8")

    facade = LegalDocPlatformFacade()
    monkeypatch.setattr(
        facade, "_get_client",
        lambda: _mock_client(
            json.dumps({"doc_type": "Постановление о возбуждении исполнительного производства"})
        ),
    )

    extracted = facade.extract_document_data(str(doc), doc_type="enforcement_orders")
    assert extracted["doc_type"] == "Постановление о возбуждении исполнительного производства"
    assert "_doc_type_normalized" not in extracted


def test_b2_plugin_without_doctype_field_is_untouched(tmp_path, monkeypatch):
    """Плагины, не объявляющие doc_type в схеме, не проходят нормализацию:
    ключ выбрасывается схемой (extra='ignore') и не участвует в контракте."""
    doc = tmp_path / "prikaz.txt"
    doc.write_text("ПРИКАЗ о приеме на работу. Табельный номер 4218.", encoding="utf-8")

    facade = LegalDocPlatformFacade()
    monkeypatch.setattr(
        facade, "_get_client",
        lambda: _mock_client(json.dumps({"doc_type": "unknown", "order_number": "12"})),
    )

    extracted = facade.extract_document_data(str(doc), doc_type="hr_orders")
    assert "_doc_type_normalized" not in extracted


def test_b2_process_single_document_raises_warning(tmp_path, monkeypatch):
    """Сквозной прогон: шапка верна, тело восстановлено, в validation.issues
    виден WARNING, validation.passed = False (Правило 1: не тихо)."""
    doc = tmp_path / "ispolnitelny_list.txt"
    doc.write_text("ИСПОЛНИТЕЛЬНЫЙ ЛИСТ. Дело № 2-1234/2020.", encoding="utf-8")

    facade = LegalDocPlatformFacade()
    monkeypatch.setattr(
        facade, "_get_client",
        lambda: _mock_client(json.dumps({"doc_type": "unknown", "case_number": "2-1234/2020"})),
    )
    monkeypatch.setattr(facade, "_reference_text_for_gate", lambda *a, **k: (None, None))
    facade.results_dir = str(tmp_path / "results")

    result = facade.process_single_document(str(doc), doc_type="executive_documents")

    assert result["doc_type"] == "executive_documents"  # шапка не тронута
    assert result["data"]["doc_type"] == "Исполнительный лист"
    assert result["data"]["_doc_type_normalized"] == "unknown"
    issues = [i for i in result["validation"]["issues"] if i["field"] == "doc_type"]
    assert issues and issues[0]["severity"] == "WARNING"
    assert "unknown" in issues[0]["message"]
    assert result["validation"]["passed"] is False


def test_b2_plugin_doctype_default_helper():
    """Каноническое значение берётся из декларации плагина (default поля)."""
    exec_doc = _plugin_doc_type_default(get_registry().get("executive_documents"))
    enforce = _plugin_doc_type_default(get_registry().get("enforcement_orders"))
    hr = _plugin_doc_type_default(get_registry().get("hr_orders"))  # поля нет
    universal = _plugin_doc_type_default(get_registry().get(UNKNOWN_CATEGORY))

    assert exec_doc == "Исполнительный лист"
    assert enforce == "Заявление взыскателя о возбуждении ИП"
    assert hr is None
    assert universal is None


# =========================================================================
# Б3: экспорт 1С берёт тип из шапки результата
# =========================================================================
def test_b3_flat_1c_doctype_comes_from_header():
    """DocType в плоской записи — маршрутизация (шапка), а не data.doc_type:
    заглушка в теле не заражает выгрузку 1С."""
    from scan_reader.core.json_exporter import convert_to_flat_1c

    doc_item = {
        "file_name": "doc.pdf",
        "doc_type": "executive_documents",
        "doc_type_title": "Исполнительные документы",
        "data": {"doc_type": "unknown", "case_number": "2-1234/2020"},
    }
    flat = convert_to_flat_1c(doc_item)
    assert flat["DocType"] == "executive_documents"


# =========================================================================
# Б4: роутер-вето
# =========================================================================
def _write_salary_doc(tmp_path):
    doc = tmp_path / "doc_030.txt"
    doc.write_text(
        "Постановление об обращении взыскания на заработную плату должника.",
        encoding="utf-8",
    )
    return doc


def test_b4_weak_router_cannot_override_confident_heuristic(tmp_path, monkeypatch):
    """Гипотеза текстового маркера (0.92) не перекрывается роутером с
    conf ниже порога: маршрут остаётся верным."""
    facade = LegalDocPlatformFacade()
    monkeypatch.delenv("VLM_ROUTER_OVERRIDE_MIN", raising=False)
    monkeypatch.setattr(
        facade, "classify_via_vlm",
        lambda *a, **k: ("hr_orders", 0.6, "vlm_dual_zone_router", {}),
    )

    doc_type, conf, method = facade.classify_document(str(_write_salary_doc(tmp_path)))
    assert doc_type == "salary_deductions"
    assert conf == 0.92
    assert method == "text_content_marker"


def test_b4_confident_router_still_overrides(tmp_path, monkeypatch):
    """Достаточно уверенный ответ роутера (>= 0.85) по-прежнему может
    опровергнуть гипотезу 0.92 — поведение P2.5 сохранено."""
    facade = LegalDocPlatformFacade()
    monkeypatch.delenv("VLM_ROUTER_OVERRIDE_MIN", raising=False)
    monkeypatch.setattr(
        facade, "classify_via_vlm",
        lambda *a, **k: ("enforcement_orders", 0.93, "vlm_dual_zone_router", {}),
    )

    doc_type, _conf, method = facade.classify_document(str(_write_salary_doc(tmp_path)))
    assert doc_type == "enforcement_orders"
    assert method == "vlm_dual_zone_router"


def test_b4_override_threshold_is_configurable(tmp_path, monkeypatch):
    """VLM_ROUTER_OVERRIDE_MIN поднимает планку вплоть до запрета
    перекрытия уверенной эвристики."""
    facade = LegalDocPlatformFacade()
    monkeypatch.setenv("VLM_ROUTER_OVERRIDE_MIN", "0.99")
    monkeypatch.setattr(
        facade, "classify_via_vlm",
        lambda *a, **k: ("enforcement_orders", 0.93, "vlm_dual_zone_router", {}),
    )

    doc_type, conf, method = facade.classify_document(str(_write_salary_doc(tmp_path)))
    assert doc_type == "salary_deductions"
    assert method == "text_content_marker"


def test_b4_weak_router_still_used_without_heuristic(tmp_path, monkeypatch):
    """Без эвристики вердикт роутера остаётся рабочим результатом —
    veto включается только для защиты уверенной гипотезы."""
    facade = LegalDocPlatformFacade()
    monkeypatch.delenv("VLM_ROUTER_OVERRIDE_MIN", raising=False)
    monkeypatch.setattr(
        facade, "classify_via_vlm",
        lambda *a, **k: ("hr_orders", 0.6, "vlm_dual_zone_router", {}),
    )
    # Путь без ключей, без текстовых маркеров: heuristic-кандидата нет.
    doc = tmp_path / "scan_001.jpg"
    doc.write_bytes(b"\xff\xd8\xff\xe0dummy")

    doc_type, _conf, method = facade.classify_document(str(doc))
    assert doc_type == "hr_orders"
    assert method == "vlm_dual_zone_router"

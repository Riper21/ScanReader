# -*- coding: utf-8 -*-
"""
Модуль комплексной метрической оценки качества распознавания юридических документов для ScanReader.
Рассчитывает процентную точность (% Quality Score / Accuracy / Completeness):
1. В режиме бенчмарка (Benchmark vs Ground Truth): сравнение AI-извлечения с эталоном.
2. В автономном режиме (Autonomous Quality & Completeness & Guardrails Validation):
   оценка полноты заполнения схемы, валидности форматов (даты, номера дел, бланки),
   проверка контрольных разрядов ИНН (ФНС) и балансовой непротиворечивости сумм.

Формирует:
- JSON (.json) структурированные метрические файлы для API и интеграций:
  - run_metrics_summary.json (сводка запуска)
  - {doc_type}_quality_metrics.json (детализация по категории)
  - metrics_history.json (накопительная история качества)
- Markdown (.md) сводный отчет run_metrics_summary.md

Excel-сводка по документам формируется отдельно (excel_exporter,
Сводный_реестр_документов.xlsx); отдельная xlsx-сводка метрик удалена в 0.9.3.
"""

import os
import re
import json
import difflib
import datetime
from typing import Dict, Any, List, Optional, Tuple

from .utils import get_logger
from .io_utils import write_atomic
from .guardrails import get_nested as _get_nested, run_guardrails, validate_ip_number_format  # noqa: F401
# C-06: контрольная сумма ИНН берётся из verifier, а не дублируется в core.
# Пакет verifier зависит только от stdlib, поэтому циклического импорта нет.
from ..verifier.checksums import validate_inn
from ..verifier.math_verifier import parse_percentage_value, verify_deduction_percentage

logger = get_logger("core.metrics_evaluator")


# ==============================================================================
# 1. МАТЕМАТИЧЕСКИЕ И ТЕКСТОВЫЕ МЕТРИКИ СХОДСТВА (BENCHMARK MODE)
# ==============================================================================

def normalize_text(text: Optional[Any]) -> str:
    """Удаляет лишние пробелы, переносы строк и приводит к нижнему регистру для нечувствительного сравнения."""
    if text is None:
        return ""
    text = str(text).lower().strip()
    text = re.sub(r"\s+", " ", text)
    replacements = {
        "№": "no", "«": '"', "»": '"', "—": "-", "–": "-",
        "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o", "р": "p", "с": "c", "т": "t", "х": "x"
    }
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


def exact_match_score(pred: Any, gt: Any) -> float:
    """Оценка 100% при точном совпадении после нормализации, иначе 0%."""
    n_pred = normalize_text(pred)
    n_gt = normalize_text(gt)
    if not n_gt and not n_pred:
        return 100.0
    if not n_gt or not n_pred:
        return 0.0
    return 100.0 if n_pred == n_gt else 0.0


def text_similarity_score(pred: Optional[Any], gt: Optional[Any]) -> float:
    """Расчет схожести строк (SequenceMatcher Ratio) в диапазоне 0.0 - 100.0%."""
    n_pred = normalize_text(pred)
    n_gt = normalize_text(gt)
    if not n_gt and not n_pred:
        return 100.0
    if not n_gt or not n_pred:
        return 0.0
    if n_pred == n_gt:
        return 100.0
    ratio = difflib.SequenceMatcher(None, n_pred, n_gt).ratio()
    return round(ratio * 100.0, 2)


def numeric_proximity_score(pred: Optional[float], gt: Optional[float], tolerance: float = 0.01) -> float:
    """Оценка числовой близости сумм в диапазоне 0.0 - 100.0%."""
    if gt is None and pred is None:
        return 100.0
    if gt is None or pred is None:
        return 0.0
    try:
        p_val = float(pred)
        g_val = float(gt)
    except (ValueError, TypeError):
        return 0.0

    if abs(p_val - g_val) <= tolerance:
        return 100.0
    diff = abs(p_val - g_val)
    denom = max(abs(g_val), 1.0)
    score = max(0.0, 1.0 - (diff / denom)) * 100.0
    return round(score, 2)


# ==============================================================================
# 1b. ЕДИНЫЙ СКОРЕР ПОЛЕЙ (C-06)
# ==============================================================================

# benchmark.json плагинов объявляет type из словаря {string, date, inn, number},
# а код понимал только {exact, numeric} и всё прочее отправлял в строковое
# сравнение. В итоге десятикратная ошибка в сумме давала 98.32% и статус
# "excellent": difflib сравнивал строки "240000" и "2400000".
FIELD_TYPE_ALIASES = {
    "exact": "exact", "strict": "exact", "id": "exact",
    "string": "text", "text": "text", "fuzzy": "text", "name": "text",
    "numeric": "numeric", "number": "numeric", "int": "numeric",
    "float": "numeric", "money": "numeric", "amount": "numeric",
    "currency": "numeric", "percentage": "numeric", "rub": "numeric",
    "inn": "inn", "snils": "inn", "ogrn": "inn", "ogrnip": "inn",
    "date": "date", "datetime": "date",
}

KNOWN_FIELD_TYPES = frozenset(FIELD_TYPE_ALIASES) | {"bool", "boolean"}


def resolve_field_type(declared: Any) -> str:
    """Приводит объявленный в benchmark.json тип поля к каноническому виду."""
    return FIELD_TYPE_ALIASES.get(str(declared or "").strip().lower(), "text")


def _to_number_loose(value: Any) -> Optional[float]:
    """Числовое значение поля: строки вида «1 234,56» и числа."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d,.\-]", "", str(value))
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        tail = s.split(",")[-1]
        s = s.replace(",", ".") if len(tail) in (1, 2) else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _normalized_date(value: Any) -> Optional[str]:
    """ISO-представление даты; None, если значение не является датой."""
    from ..verifier.chronology import parse_flexible_date

    parsed = parse_flexible_date(str(value or ""))
    return parsed.isoformat() if parsed else None


def _is_blank(value: Any) -> bool:
    """Пустое значение: None, пустая строка или строка-заглушка от VLM."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip() or value.strip().lower() in ("none", "null", "nan")
    return False


def score_field(
    pred: Any,
    gt: Any,
    declared_type: Any = None,
) -> Tuple[Optional[float], str, str]:
    """
    Сравнение одного поля извлечения с эталоном.

    :returns: (score, kind, state), где
        score  — 0..100 либо None, если поле неприменимо;
        kind   — канонический тип поля;
        state  — "scored" | "absent_both" | "hallucination" | "missed".

    Семантика знаменателя (C-06). Раньше поле, пустое с обеих сторон, давало 100.0,
    и почти пустое извлечение набирало «идеальную» точность: 8 отсутствующих полей
    при одном совпавшем. Присвоение 0.0 тоже неверно — модель не должна
    наказываться за то, что не выдумала поле, которого в документе нет.

    Поэтому знаменатель образуют поля, ПРИСУТСТВУЮЩИЕ в эталоне:
        - есть в эталоне и в извлечении — оценивается по существу;
        - есть в эталоне, нет в извлечении — промах, 0.0;
        - нет в эталоне, есть в извлечении — галлюцинация, штраф;
        - нет с обеих сторон — исключается из знаменателя.
    """
    kind = resolve_field_type(declared_type)
    pred_blank, gt_blank = _is_blank(pred), _is_blank(gt)

    if gt_blank and pred_blank:
        return None, kind, "absent_both"
    if gt_blank:
        return 0.0, kind, "hallucination"
    if pred_blank:
        return 0.0, kind, "missed"

    if kind == "numeric":
        p_num, g_num = _to_number_loose(pred), _to_number_loose(gt)
        if p_num is None or g_num is None:
            return 0.0, kind, "scored"
        return numeric_proximity_score(p_num, g_num), kind, "scored"

    if kind == "inn":
        p_digits = re.sub(r"\D", "", str(pred))
        g_digits = re.sub(r"\D", "", str(gt))
        if not p_digits or p_digits != g_digits:
            return 0.0, kind, "scored"
        ok, _msg = validate_inn(p_digits)
        return (100.0 if ok else 50.0), kind, "scored"

    if kind == "date":
        p_date, g_date = _normalized_date(pred), _normalized_date(gt)
        if p_date is None or g_date is None:
            return 0.0, kind, "scored"
        return (100.0 if p_date == g_date else 0.0), kind, "scored"

    if kind == "exact":
        return exact_match_score(pred, gt), kind, "scored"

    return text_similarity_score(pred, gt), kind, "scored"


# ==============================================================================
# 2. АВТОНОМНЫЕ ВАЛИДАТОРЫ И ПРОВЕРКИ РЕКВИЗИТОВ (GUARDRAILS)
# ==============================================================================

def validate_inn_string(details_str: Optional[str]) -> Tuple[bool, Optional[str], float]:
    """
    Проверяет наличие корректного ИНН (10 или 12 знаков) по контрольным разрядам ФНС.

    C-06: контрольная сумма считается единственным источником правды —
    verifier.checksums.validate_inn. Прежняя локальная копия алгоритма
    возвращала (True, <первый найденный>, 85.0) даже когда контрольный разряд
    НЕ совпадал, то есть заведомо невалидный ИНН получал 85 баллов и
    is_valid=True, а результат шёл в итоговый Quality Score.
    """
    if not details_str:
        return False, None, 0.0
    matches = re.findall(r"\b(\d{10}|\d{12})\b", str(details_str))
    if not matches:
        return False, None, 50.0  # Реквизиты есть, но без ИНН

    for inn in matches:
        ok, _msg = validate_inn(inn)
        if ok:
            return True, inn, 100.0
    # Найдены похожие на ИНН числа, но ни одно не проходит контрольный разряд.
    return False, matches[0], 0.0


def validate_case_number_format(case_num: Optional[str]) -> Tuple[bool, float]:
    """
    Проверяет формат судебного дела (А40-1234/2021, 2-123/2020 и т.д.).
    S-12: длинные номера без разделителей («-», «/») подозрительны —
    VLM мог склеить или исказить номер; оценка снижается до 60.
    """
    if not case_num:
        return False, 0.0
    c_str = str(case_num).strip()
    pattern = r"^[А-Яа-я0-9A-Za-z\s\№\-\–\—\/\.]+$"
    if re.match(pattern, c_str) and len(c_str) >= 4 and any(c.isdigit() for c in c_str):
        if len(c_str) >= 6 and "/" not in c_str and "-" not in c_str and "–" not in c_str:
            return True, 60.0
        return True, 100.0
    return False, 40.0



# validate_ip_number_format переехал в core.guardrails (импортируется выше),
# чтобы существовал единственный исполнитель правил autonomous.json.
# re-export сохранён для обратной совместимости внешних вызовов.


_MONTH_NAMES = (
    "январ", "феврал", "март", "апрел",
    "ма", "июн", "июл", "август",
    "сентябр", "октябр", "ноябр", "декабр",
)


def validate_date_string(date_str: Optional[str]) -> Tuple[bool, float]:
    """
    Проверяет дату на соответствие формату.

    Принимаются ДД.ММ.ГГГГ, ДД/ММ/ГГГГ, ISO ГГГГ-ММ-ДД и словесные формы
    («15 марта 2021»). До исправления ISO-дата не проходила: якорь \\b перед
    однозначным днём не мог начать матч внутри «2021-03-15», а словесной ветке
    месяц был недоступен при отсутствии букв.

    C-06: месяц «мая» раньше проверялся подстрокой "ма", что совпадало с любым
    словом, содержащим «ма» — «сумма», «компания», «норма», «власть».
    """
    if not date_str:
        return False, 0.0
    d_str = str(date_str).strip()
    # ISO: ГГГГ-ММ-ДД
    if re.match(r"^\d{4}-\d{2}-\d{2}([T ]|$)", d_str):
        return True, 100.0
    # ДД.ММ.ГГГГ / ДД/ММ/ГГГГ / ДД-ММ-ГГ
    m = re.search(r"(?<!\d)(\d{1,2})[\.\/\-](\d{1,2})[\.\/\-](\d{2,4})(?!\d)", d_str)
    if m:
        return True, 100.0
    lowered = d_str.lower()
    for month in _MONTH_NAMES:
        # «ма» требует либо начало слова/разделитель, либо окончание «мая»/«мае»
        if month == "ма":
            if re.search(r"(?<![\w])ма(?:я|е|й|ю)?\b", lowered):
                return True, 95.0
        elif re.search(rf"{month}\w*\b", lowered):
            return True, 95.0
    return False, 20.0


def validate_math_balance(
    main_debt: Optional[float],
    penalty: Optional[float],
    fee: Optional[float],
    other: Optional[float],
    total: Optional[float]
) -> Tuple[bool, float, float]:
    """
    Проверяет математический баланс:
    Основной долг + Пени + Госпошлина + Прочие = ИТОГО (с допуском 0.05 руб).
    Возвращает (is_balanced, diff_rub, score_percent).
    """
    has_total = total is not None and isinstance(total, (int, float)) and total > 0
    components = [v for v in [main_debt, penalty, fee, other] if v is not None and isinstance(v, (int, float))]

    if not has_total and not components:
        return True, 0.0, 100.0

    if has_total and not components:
        return True, 0.0, 95.0

    calc_sum = sum(c for c in components if c is not None)
    if has_total and total is not None:
        total_f = float(total)
        diff = abs(calc_sum - total_f)
        if diff <= 0.05:
            return True, 0.0, 100.0
        elif diff <= 1.0:
            return True, diff, 90.0
        else:
            denom = max(total_f, 1.0)
            score = max(0.0, 1.0 - (diff / denom)) * 100.0
            return False, round(diff, 2), round(score, 2)
    else:
        return True, 0.0, 90.0


def validate_deduction_rate(rate_str: Optional[str], claim_subject: str = "") -> Tuple[bool, float]:
    """
    Проверка процента удержания по ст. 99 229-ФЗ.

    C-06: функция была проверкой наличия ключевых слов и дублировала
    verifier.math_verifier, с которым при этом расходилась: любое значение с
    «доля» в тексте получало 90.0, поэтому «80%» и «100%» — заведомо
    незаконные величины — проходили как корректные. Теперь используется
    единственный алгоритмический источник правды.
    """
    if not rate_str:
        return False, 0.0
    subject = str(claim_subject or "").lower()
    has_basis = any(
        marker in subject
        for marker in ("алимент", "несовершеннолетн", "ребен", "содержание", "вред")
    )
    ok, _msg = verify_deduction_percentage(str(rate_str), has_alimony_or_harm=has_basis)
    value = parse_percentage_value(str(rate_str))
    if ok:
        return True, 100.0
    # Превышение 70% — безусловное нарушение закона, между 50% и 70% требует
    # основания, остальное — подозрительно, но не заведомо неверно.
    if value is not None and value > 70.0:
        return False, 0.0
    return False, 40.0


# ==============================================================================
# 3. ОЦЕНКА ДОКУМЕНТОВ В РЕЖИМЕ BENCHMARK (VS GROUND TRUTH)
# ==============================================================================

def evaluate_generic_benchmark(
    pred: Dict[str, Any],
    gt: Dict[str, Any],
    doc_type: Optional[str] = None
) -> Dict[str, Any]:
    """
    Универсальная оценка в режиме Benchmark (vs Ground Truth) на основе benchmark.json плагина.
    (Устраняет дефект C-06: исключает ложный fallback на executive_documents).
    """
    scores: Dict[str, float] = {}
    weights: Dict[str, float] = {}

    plugin = None
    if doc_type:
        from ..type_registry import get_registry
        try:
            plugin = get_registry().get(doc_type)
        except Exception as e:
            logger.debug(f"Не удалось загрузить плагин '{doc_type}' для оценки: {e}")

    bench_fields = plugin.benchmark_config.get("fields", []) if plugin else []
    absent_on_both: List[str] = []
    hallucinated: List[str] = []
    missed: List[str] = []
    gt_weight = 0.0
    earned = 0.0
    penalty = 0.0

    if bench_fields:
        for f in bench_fields:
            path = f.get("path", "")
            if not path:
                continue
            weight = float(f.get("weight", 1.0))
            label = f.get("label", path)

            sim, kind, state = score_field(_get_nested(pred, path), _get_nested(gt, path), f.get("type"))
            if state == "absent_both":
                absent_on_both.append(label)
                continue
            if state == "hallucination":
                hallucinated.append(label)
                penalty += weight
                scores[label] = 0.0
                continue
            if state == "missed":
                missed.append(label)
            gt_weight += weight
            earned += (sim or 0.0) * weight / 100.0
            scores[label] = round(sim or 0.0, 2)
            weights[label] = weight
    else:
        # Fallback при отсутствии конфига: динамическое попарное сравнение ключей эталона
        for k, gt_val in gt.items():
            if k in ("file_name", "doc_type"):
                continue
            declared = "numeric" if isinstance(gt_val, (int, float)) and not isinstance(gt_val, bool) else None
            sim, _kind, state = score_field(pred.get(k), gt_val, declared)
            if state == "absent_both":
                absent_on_both.append(k)
                continue
            if state == "hallucination":
                hallucinated.append(k)
                penalty += 1.0
                continue
            if state == "missed":
                missed.append(k)
            gt_weight += 1.0
            earned += (sim or 0.0) / 100.0
            scores[k] = round(sim or 0.0, 2)
            weights[k] = 1.0

    # C-06: знаменатель — вес полей, реально присутствующих в эталоне. При
    # отсутствии таких полей оценка не состоялась и не должна выглядеть как
    # идеальная точность.
    overall = (earned - penalty) / gt_weight * 100.0 if gt_weight > 0 else 0.0
    overall = max(0.0, min(100.0, overall))

    return {
        "file_name": gt.get("file_name", pred.get("file_name", "")),
        "mode": "benchmark",
        "overall_score": round(overall, 2),
        "field_scores": scores,
        "evaluator": "generic",
        "benchmark_config_consumed": bool(bench_fields),
        "fields_in_ground_truth": int(gt_weight),
        "fields_absent_on_both_sides": absent_on_both,
        "fields_hallucinated": hallucinated,
        "fields_missed": missed,
        "hallucination_penalty": round(penalty, 2),
        "validation_passed": bool(overall >= 70.0 and not hallucinated),
    }


def evaluate_generic_autonomous(
    doc: Dict[str, Any],
    doc_type: Optional[str] = None
) -> Dict[str, Any]:
    """
    Универсальная автономная оценка качества на основе autonomous.json плагина.

    C-06: используется тот же core.guardrails.run_guardrails, что и в
    LegalDocPlatformFacade.validate_document. Раньше здесь жила вторая копия
    правил с другим словарём и веткой «просто проверь на непустоту» для всего
    остального, из-за чего одно поле получало противоположные вердикты в двух
    путях, а нереализованные правила молча считались пройденными.
    """
    plugin = None
    if doc_type:
        from ..type_registry import get_registry
        try:
            plugin = get_registry().get(doc_type)
        except Exception as e:
            logger.debug(f"Не удалось загрузить плагин '{doc_type}' для оценки: {e}")

    rules = plugin.autonomous_config.get("fields", []) if plugin else []
    result = run_guardrails(rules, doc, plugin_id=plugin.id if plugin else "")

    scores = {issue["field"]: 0.0 for issue in result["issues"]}
    weights = {issue["field"]: 1.0 for issue in result["issues"]}
    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        f_path = rule.get("field", "")
        if f_path and f_path not in scores:
            scores[f_path] = 100.0
            weights[f_path] = 1.0

    if not rules:
        # Completeness fallback: проверка заполненности ключей
        scores, weights = {}, {}
        for k, v in doc.items():
            if k in ("file_name", "doc_type"):
                continue
            scores[k] = 100.0 if v not in (None, "", [], {}) else 0.0
            weights[k] = 1.0

    total_weight = sum(weights.values())
    # C-06: при пустом наборе правил оценка не состоялась и не равна 100.0.
    weighted_total = (sum((scores[k] * (weights[k] / total_weight)) for k in scores)
                      if total_weight > 0 else 0.0)

    return {
        "file_name": doc.get("file_name", ""),
        "mode": "autonomous",
        "overall_score": round(weighted_total, 2),
        "field_scores": scores,
        "evaluator": "generic",
        "autonomous_config_consumed": bool(rules),
        "rules_unknown": result["rules_unknown"],
        "evaluator_degraded": total_weight == 0,
        "validation_passed": bool(weighted_total >= 70.0 and result["passed"]),
    }


def get_benchmark_evaluator(doc_type: Optional[str] = None):
    """
    Оценщик бенчмарка для типа документа.

    C-06: раньше здесь была таблица BENCHMARK_EVALUATORS с девятью
    захардкоженными ID, где трём приоритетным типам доставались частные
    скореры, игнорировавшие СВОЙ ЖЕ benchmark.json плагина. Частные скореры
    удалены вместе с таблицами: все девять типов оцениваются по данным
    плагина, как и обещает контракт из семи файлов (CHANGELOG C-06).
    """
    return lambda p, g: evaluate_generic_benchmark(p, g, doc_type=doc_type)


def get_autonomous_evaluator(doc_type: Optional[str] = None):
    """Автономный оценщик для типа документа (единый движок core.guardrails)."""
    return lambda d: evaluate_generic_autonomous(d, doc_type=doc_type)


def get_status_from_score(score: float) -> str:
    """Определяет категорию качества по шкале 0..100%."""
    if score >= 95.0:
        return "excellent"
    elif score >= 85.0:
        return "high"
    elif score >= 70.0:
        return "satisfactory"
    return "needs_attention"


# ==============================================================================
# 5. ГЛАВНЫЙ МЕНЕДЖЕР ОЦЕНКИ КАТЕГОРИИ
# ==============================================================================

def evaluate_dataset(
    predicted_docs: Any,
    ground_truth_docs: Optional[Any] = None,
    doc_type: Optional[Any] = None,
    registry: Optional[Any] = None
) -> Any:
    """
    Универсальная оценка датасета:
    - Поддерживает как списки документов (List[Dict]), так и пути к каталогам (str, Path) для MCP и CLI (C-08).
    - Если передан ground_truth_docs: сопоставляет по имени файла и рассчитывает Benchmark Quality Score (%).
    - Если ground_truth_docs не передан или файл в нем отсутствует: выполняет автономную оценку Autonomous Quality & Guardrails (%).
    - Для всех 9 типов документов использует целевые правила плагинов, исключая ложный fallback на executive_documents (C-06).
    """
    from pathlib import Path
    import glob

    # Разрешение параметров при вызове из MCP: evaluate_dataset(results_dir, gt_dir, registry)
    if registry is None and doc_type is not None and not isinstance(doc_type, str):
        registry = doc_type
        doc_type = None

    # Обработка передачи путей каталогов (MCP / Batch mode)
    if isinstance(predicted_docs, (str, Path)) and os.path.isdir(str(predicted_docs)):
        results_dir = Path(predicted_docs)
        gt_dir = Path(ground_truth_docs) if ground_truth_docs and os.path.isdir(str(ground_truth_docs)) else None

        results_map: Dict[str, List[Dict[str, Any]]] = {}
        for jf in glob.glob(str(results_dir / "*.json")):
            base = os.path.basename(jf)
            if base.startswith("run_metrics") or base.startswith("benchmark_") or base.endswith("_quality_metrics.json"):
                continue
            try:
                with open(jf, "r", encoding="utf-8") as f:
                    d = json.load(f)
                    items = d if isinstance(d, list) else [d]
                    for it in items:
                        if isinstance(it, dict):
                            dt = it.get("doc_type", "unknown")
                            results_map.setdefault(dt, []).append(it)
            except Exception as e:
                logger.debug(f"Пропущен некорректный JSON файл '{jf}': {e}")

        if registry is None:
            from ..type_registry import get_registry
            registry = get_registry()

        category_metrics: Dict[str, Dict[str, Any]] = {}
        all_doc_types = set(results_map.keys())
        if registry and hasattr(registry, "enabled"):
            all_doc_types.update(registry.enabled().keys())

        for cat_id in all_doc_types:
            if doc_type and cat_id != doc_type:
                continue
            cat_preds = results_map.get(cat_id, [])
            cat_gt = None
            cat_plugin = registry.get(cat_id) if registry is not None else None
            if gt_dir and registry and hasattr(registry, "enabled") and cat_id in registry.enabled() and cat_plugin is not None:
                gt_file = cat_plugin.gt_file
                gt_fpath = gt_dir / gt_file
                if gt_fpath.is_file():
                    try:
                        with open(gt_fpath, "r", encoding="utf-8") as gf:
                            cat_gt = json.load(gf)
                    except Exception as e:
                        logger.debug(f"Не удалось прочитать эталон '{gt_fpath}': {e}")
                        cat_gt = None
            elif gt_dir:
                candidate = gt_dir / f"{cat_id}.json"
                if candidate.is_file():
                    try:
                        with open(candidate, "r", encoding="utf-8") as gf:
                            cat_gt = json.load(gf)
                    except Exception as e:
                        logger.debug(f"Не удалось прочитать эталон '{candidate}': {e}")
                        cat_gt = None

            if cat_preds or cat_gt:
                category_metrics[cat_id] = evaluate_dataset(cat_preds, cat_gt, doc_type=cat_id)

        if doc_type and doc_type in category_metrics:
            return category_metrics[doc_type]
        return category_metrics

    # Режим списка документов
    doc_list: List[Dict[str, Any]] = predicted_docs if isinstance(predicted_docs, list) else []

    doc_type_str: str
    if not doc_type and doc_list:
        first_doc = doc_list[0]
        first_data: Any = first_doc.get("data")
        p_check: Dict[str, Any] = first_data if isinstance(first_data, dict) else first_doc
        doc_type_str = str(p_check.get("doc_type") or first_doc.get("doc_type") or "generic")
    elif doc_type:
        doc_type_str = str(doc_type)
    else:
        doc_type_str = "generic"

    bench_func = get_benchmark_evaluator(doc_type_str)
    auto_func = get_autonomous_evaluator(doc_type_str)

    gt_map: Dict[str, Dict[str, Any]] = {}
    if ground_truth_docs and isinstance(ground_truth_docs, list):
        for d in ground_truth_docs:
            if isinstance(d, dict) and "file_name" in d:
                gt_map[d["file_name"]] = d

    evaluated_docs = []
    status_counts = {"excellent": 0, "high": 0, "satisfactory": 0, "needs_attention": 0}
    total_scores = []

    for raw_p in doc_list:
        if not isinstance(raw_p, dict):
            continue
        p_val: Any = raw_p.get("data")
        p: Dict[str, Any] = p_val if isinstance(p_val, dict) else raw_p
        f_name = p.get("file_name") or raw_p.get("file_name", "")

        # C-06: режим фиксируется по факту применения бенчмарк-скоринга, а не по
        # непустоте gt_map. Раньше несовпадение имён тихо переключало документ на
        # автономную оценку, а отчёт продолжал называться "benchmark".
        if gt_map and f_name in gt_map:
            doc_res = bench_func(p, gt_map[f_name])
            mode_used = "benchmark"
        else:
            doc_res = auto_func(p)
            mode_used = "autonomous_no_ground_truth_match" if gt_map else "autonomous"

        score = doc_res["overall_score"]
        status = get_status_from_score(score)
        doc_res["status"] = status
        doc_res["file_name"] = f_name
        doc_res["mode_used"] = mode_used
        doc_res["measurement_caveats"] = _measurement_caveats(doc_res, mode_used, bool(gt_map))

        status_counts[status] += 1
        total_scores.append(score)
        evaluated_docs.append(doc_res)

    avg_score = round(sum(total_scores) / len(total_scores), 2) if total_scores else 0.0
    benchmark_used = sum(1 for d in evaluated_docs if d.get("mode_used") == "benchmark")
    gt_documents_available = len(gt_map) if gt_map else 0

    return {
        "doc_type": doc_type_str,
        "total_documents": len(evaluated_docs),
        "mode": "benchmark" if gt_map else "autonomous",
        "average_quality_score_percent": avg_score,
        "status_counts": status_counts,
        "documents_measured_in_benchmark_mode": benchmark_used,
        "ground_truth_documents_available": gt_documents_available,
        "ground_truth_coverage_percent": (
            round(benchmark_used / gt_documents_available * 100.0, 1) if gt_documents_available else 0.0
        ),
        "measurement_caveats": {
            "benchmark_mode_available": bool(gt_map),
            "documents_scored_against_ground_truth": benchmark_used,
            "documents_scored_autonomously": len(evaluated_docs) - benchmark_used,
            "low_confidence": len(evaluated_docs) > 0 and benchmark_used == 0 and bool(gt_map),
        },
        "documents": evaluated_docs
    }


def _measurement_caveats(doc_res: Dict[str, Any], mode_used: str, gt_available: bool) -> Dict[str, Any]:
    """
    C-06: что именно стоит за числом.

    Одинокий процент без контекста вводит в заблуждение: «100.00%» на одном
    заполненном поле с одним эталонным документом не является характеристикой
    качества модели. Явные оговорки позволяют оператору это увидеть.
    """
    fields_in_gt = doc_res.get("fields_in_ground_truth")
    caveats: Dict[str, Any] = {
        "mode_used": mode_used,
        "evaluator": doc_res.get("evaluator", "generic"),
        "benchmark_config_consumed": doc_res.get("benchmark_config_consumed", False),
        "autonomous_config_consumed": doc_res.get("autonomous_config_consumed", False),
        "fields_in_ground_truth": fields_in_gt,
        "fields_absent_on_both_sides": len(doc_res.get("fields_absent_on_both_sides", []) or []),
        "fields_hallucinated": doc_res.get("fields_hallucinated", []),
        "fields_missed": doc_res.get("fields_missed", []),
        "evaluator_degraded": bool(doc_res.get("evaluator_degraded")),
    }
    caveats["ground_truth_match"] = gt_available and mode_used == "benchmark"
    # Малый знаменатель: цифра держится на одном-двух полях
    caveats["low_field_support"] = isinstance(fields_in_gt, int) and 0 < fields_in_gt <= 2
    return caveats


def generate_run_summary(all_category_metrics: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    """Формирует общую сводку запуска (Run Summary) по всем категориям документов."""
    # Защита: если передан отчет по одной категории напрямую (C-08)
    if isinstance(all_category_metrics, dict) and "documents" in all_category_metrics and "average_quality_score_percent" in all_category_metrics:
        dt = str(all_category_metrics.get("doc_type", "default"))
        wrapped: Dict[str, Dict[str, Any]] = {dt: all_category_metrics}
        all_category_metrics = wrapped

    total_docs = 0
    all_scores = []
    status_dist = {"excellent": 0, "high": 0, "satisfactory": 0, "needs_attention": 0}
    categories_summary = {}
    measured_vs_gt = 0
    measured_autonomously = 0
    gt_available_total = 0
    low_confidence_categories = []

    for cat_name, metrics in all_category_metrics.items():
        if not isinstance(metrics, dict):
            continue
        doc_count = metrics.get("total_documents", 0)
        total_docs += doc_count
        c_status = metrics.get("status_counts", {})
        for k in status_dist:
            status_dist[k] += c_status.get(k, 0)

        for d in metrics.get("documents", []):
            all_scores.append(d.get("overall_score", 0.0))

        # Фаза 9.2: сводка запуска ТЕРЯЛА честные метрики измерения, которые
        # evaluate_dataset формирует с Фазы 5.5. Оператор читал «среднее 100%»
        # без сведений о том, сколько документов мерилось против эталона, а
        # сколько — автономно, и на каком числе полей держится цифра.
        measured_vs_gt += int(metrics.get("documents_measured_in_benchmark_mode", 0) or 0)
        measured_autonomously += int(metrics.get("total_documents", 0) or 0) - int(
            metrics.get("documents_measured_in_benchmark_mode", 0) or 0
        )
        gt_available_total += int(metrics.get("ground_truth_documents_available", 0) or 0)
        if (metrics.get("measurement_caveats") or {}).get("low_confidence"):
            low_confidence_categories.append(cat_name)

        categories_summary[cat_name] = {
            "total_documents": doc_count,
            "average_quality_score_percent": metrics.get("average_quality_score_percent", 0.0),
            "mode": metrics.get("mode", "autonomous"),
            "status_counts": c_status,
            "documents_measured_in_benchmark_mode": metrics.get("documents_measured_in_benchmark_mode", 0),
            "ground_truth_documents_available": metrics.get("ground_truth_documents_available", 0),
            "ground_truth_coverage_percent": metrics.get("ground_truth_coverage_percent", 0.0),
            "measurement_caveats": metrics.get("measurement_caveats", {}),
        }

    overall_score = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0

    return {
        "timestamp": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_documents_processed": total_docs,
        "overall_quality_score_percent": overall_score,
        "status_distribution": status_dist,
        # Фаза 9.2: сводный показатель точности без оговорок вводит в заблуждение,
        # когда эталонов нет вовсе или они покрывают лишь часть документов.
        "overall_accuracy_is_real": bool(measured_vs_gt) and not low_confidence_categories,
        "measurement_caveats": {
            "documents_measured_against_ground_truth": measured_vs_gt,
            "documents_measured_autonomously": measured_autonomously,
            "ground_truth_documents_available": gt_available_total,
            "ground_truth_coverage_percent": (
                round(measured_vs_gt / gt_available_total * 100.0, 1) if gt_available_total else 0.0
            ),
            "categories_without_measured_benchmark": low_confidence_categories,
        },
        "categories": categories_summary
    }


# ==============================================================================
# 6. ЭКСПОРТ ОТЧЕТОВ: JSON, MARKDOWN, EXCEL
# ==============================================================================

def export_metrics_json(data: Dict[str, Any], filepath: str):
    """Экспортирует метрический отчет в JSON файл (атомарно, M-19)."""
    dir_path = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(dir_path, exist_ok=True)
    write_atomic(filepath, json.dumps(data, ensure_ascii=False, indent=2))


def append_to_metrics_history(run_summary: Dict[str, Any], history_filepath: str):
    """Добавляет сводку запуска в накопительную историю metrics_history.json (атомарно, M-19)."""
    dir_path = os.path.dirname(os.path.abspath(history_filepath))
    os.makedirs(dir_path, exist_ok=True)

    history = []
    if os.path.exists(history_filepath):
        try:
            with open(history_filepath, "r", encoding="utf-8") as f:
                history = json.load(f)
                if not isinstance(history, list):
                    history = []
        except Exception as e:
            logger.warning(f"Не удалось прочитать историю метрик '{history_filepath}' (перезаписывается): {e}")
            history = []

    history.append(run_summary)
    write_atomic(history_filepath, json.dumps(history, ensure_ascii=False, indent=2))


def export_run_summary_markdown(summary: Dict[str, Any], filepath: str):
    """Экспортирует читаемый Markdown-отчет о качестве запуска."""
    dir_path = os.path.dirname(os.path.abspath(filepath))
    os.makedirs(dir_path, exist_ok=True)

    score = summary.get("overall_quality_score_percent", 0.0)
    total = summary.get("total_documents_processed", 0)
    st = summary.get("status_distribution", {})

    md = [
        "# 📊 Сводный отчет о качестве распознавания документов (Run Metrics)",
        f"> **Дата запуска:** {summary.get('timestamp')}  ",
        f"> **Обработано документов:** {total}  ",
        f"> **Итоговый Quality Score:** **{score}%**  \n",
        "## 1. Распределение качества документов",
        f"- 🟢 **Отличное качество (>= 95%):** {st.get('excellent', 0)}",
        f"- 🟡 **Высокое качество (85-94%):** {st.get('high', 0)}",
        f"- 🟠 **Удовлетворительное (70-84%):** {st.get('satisfactory', 0)}",
        f"- 🔴 **Требует внимания (< 70%):** {st.get('needs_attention', 0)}\n",
        "## 2. Результаты по категориям",
        "| Категория | Документов | Режим оценки | Quality Score (%) |",
        "| :--- | :---: | :---: | :---: |"
    ]

    for cat_k, cat_v in summary.get("categories", {}).items():
        md.append(f"| `{cat_k}` | {cat_v.get('total_documents')} | {cat_v.get('mode')} | **{cat_v.get('average_quality_score_percent')}%** |")

    md.append("\n---\n*Отчет сгенерирован автоматически AI-Системой ScanReader.*")

    write_atomic(filepath, "\n".join(md))


# export_run_summary_excel удалён в 0.9.3: сводный Excel по типам документов
# формирует LegalExcelExporter (Сводный_реестр_документов.xlsx), а отдельная
# xlsx-сводка метрик была его дубликатом. Числа запуска живут в
# run_metrics_summary.json/.md и metrics_history.json.

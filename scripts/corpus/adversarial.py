# -*- coding: utf-8 -*-
"""
Генератор адверсарных вариаций эталонного корпуса.

Зачем. Открытые риски C.1-C.5 требуют измерения, а реального корпуса сканов ФССП
в репозитории нет и быть не должно: это персональные данные. Вместо него
строятся АДВЕРСАРНЫЕ ВАРИАЦИИ существующих обезличенных эталонов: они
воспроизводят те самые дефекты, которые дают настоящие плохие сканы, но не
содержат ПДн.

Что воспроизводится:
  * ошибки распознавания цифр и букв (0/О, 1/7, 3/8, 5/6, 8/В, 2/Z);
  * пропуск и задвоение символов;
  * «грязные» имена файлов из реальной бухгалтерской практики;
  * многостраничные документы с ключевыми полями далеко за пределом лимита.

Важно: шум вносится ТОЛЬКО в эталонный текст, а извлечённые данные остаются
чистыми. Это и есть сценарий ложного срабатывания: модель прочитала верно,
OCR-эталон прочитал неверно, гейт увидел расхождение.

Все генераторы детерминированы (seed), результат воспроизводим.
"""

from __future__ import annotations

import random
from typing import Dict, Iterable, List, Optional, Sequence

#: Реальные путаницы распознавания, наблюдаемые на сканах ФССП и в УПД.
OCR_CONFUSIONS: Dict[str, str] = {
    "0": "Оо",
    "1": "Іі",
    "2": "Zz",
    "3": "Зз",
    "4": "Чч",
    "5": "Бб",
    "6": "Ьь",
    "7": "Тт",
    "8": "Вв",
    "9": "Gg",
    "р": "р",
    "к": "к",
    "о": "о",
    "а": "а",
    "е": "е",
    "с": "с",
    "х": "х",
    "у": "у",
    "и": "и",
    "н": "п",
    "м": "ш",
    "д": "л",
    "г": "т",
    "ш": "щ",
    "ь": "ъ",
}

#: «Грязные» имена файлов, характерные для реального архива.
MESSY_FILENAMES: Dict[str, List[str]] = {
    "salary_deductions": [
        "IMG_20240115_143022.jpg",
        "Скан0001.pdf",
        "постановление_об_удержании_из_зарплаты.pdf",
        "удержание из заработной платы january.pdf",
        "ФССП_удержание_(1).pdf",
        "постановление пристава 15.01.2024.pdf",
    ],
    "enforcement_orders": [
        "Приказы_ИП_к_пробам.pdf",
        "заявление_о_возбуждении_ИП.pdf",
        "IMG_20301211_093311.jpg",
        "постановление о взыскании.docx",
        "zayavlenie_o_vozbuzhdenii.pdf",
        "ПРИКАЗ ФССП 2024.pdf",
    ],
    "executive_documents": [
        "ИЛ_ФС_002000001.pdf",
        "Исполнительный лист серия ФС.pdf",
        "IMG_0123.jpg",
        "исполнительный лист 1.pdf",
        "list_vzyskaniya.pdf",
        "Ил ИП.pdf",
    ],
    "hr_orders": [
        "ПРИКАЗ_о_принятии_на_работу_П-1.pdf",
        "приказ Т-1 о приеме.pdf",
        "кадры приказ 2024.docx",
        "Т-1 увольнение.docx",
        "IMG_4455.jpg",
        "приказ о приёме на работу.pdf",
    ],
    "invoices_upd": [
        "УПД №123 от 15.01.2024.pdf",
        "Счет-фактура 2024.pdf",
        "накладная УПД.pdf",
        "upd_123.pdf",
        "IMG_7788.jpg",
        "Торг12 15.01.2024.pdf",
    ],
    "commercial_contracts": [
        "Договор поставки №15.pdf",
        "договор аренды 2024.pdf",
        "ДОГОВОР (1).pdf",
        "contract_2024.pdf",
        "IMG_9911.jpg",
        "Договор подряда.docx",
    ],
    "legal_claims": [
        "претензия о взыскании.docx",
        "ПРЕТЕНЗИЯ Исковое заявление.pdf",
        "pretensiya.docx",
        "IMG_3322.jpg",
        "досудебная претензия.pdf",
        "Решение суда о взыскании.pdf",
    ],
    "powers_of_attorney": [
        "Доверенность.pdf",
        "доверенность нотариальная.docx",
        "POA_2024.pdf",
        "IMG_5544.jpg",
        "ДОВЕРЕННОСТЬ (копия).pdf",
        "доверенность на получение.docx",
    ],
    "acceptance_certificates": [
        "Акт приемки услуг.pdf",
        "акт выполненных работ.docx",
        "IMG_6677.jpg",
        "akt_priemki.pdf",
        "АКТ приемки (подписан).pdf",
        "акт_приемки_2024.docx",
    ],
}


def inject_ocr_noise(text: str, rate: float, seed: int = 0) -> str:
    """
    Вносит ошибки распознавания в заданной доле символов.

    Типы дефектов, реально встречающиеся на сканах:
      - подмена символа на визуально близкий (0 -> О, 8 -> В);
      - пропуск символа;
      - задвоение символа.

    :param rate: доля изменяемых символов, 0..1
    """
    if rate <= 0 or not text:
        return text

    rng = random.Random(seed)
    out: List[str] = []
    changed = 0
    for ch in text:
        if ch.isalpha() or ch.isdigit():
            roll = rng.random()
            if roll < rate:
                changed += 1
                kind = rng.random()
                options = OCR_CONFUSIONS.get(ch.lower())
                if kind < 0.55 and options:
                    out.append(rng.choice(options))
                elif kind < 0.8:
                    continue  # пропуск символа
                else:
                    out.append(ch)
                    out.append(ch)  # задвоение
                continue
        out.append(ch)
    return "".join(out)


def realistic_reference_text(
    doc: Dict[str, object],
    doc_type: str,
    seed: int = 0,
) -> str:
    """
    Собирает ЭТАЛОННЫЙ ТЕКСТ из тех же полей, которые объявляет verification.json.

    Ключевое требование: при нулевом шуме расхождений быть не должно. Текст
    строится ровно из gate_fields и gate_names проверяемого плагина, поэтому
    «чистый» эталон гарантированно согласован с извлечёнными данными, и
    единственной переменной измерения остаётся порча распознавания.

    Первая версия стенда собирала текст из вложенных полей документа и дала
    100 % ложных срабатываний даже на чистом эталоне. Причина оказалась не в
    гейте, а в том, что ЭТАЛОНЫ ОБЕЗЛИЧЕНЫ: ИНН обрезан до шести цифр
    (692519), вместо СНИЛС стоит слово «СНИЛС», а номер документа не содержит
    суммы. Гейт справедливо сообщал, что этих значений в тексте нет.

    :param doc: запись эталонного набора
    :param doc_type: идентификатор плагина, чей verification.json используется
    """
    from scan_reader.verifier.spec import resolve_spec

    spec = resolve_spec(doc_type)
    rng = random.Random(seed)
    parts: List[str] = [str(doc.get("doc_type") or "Документ").strip() or "Документ"]

    def nested(path: str) -> object:
        cur: object = doc
        for key in path.split("."):
            if not isinstance(cur, dict):
                return None
            cur = cur.get(key)
        return cur

    for item in spec.gate_fields:
        value = nested(item["path"])
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        parts.append(f"{item['path']}: {value}")

    for path in spec.gate_names:
        value = nested(path)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        parts.append(f"{path}: {value}")

    for path in spec.gate_authorities:
        value = nested(path)
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        parts.append(f"{path}: {value}")

    for item in spec.money_rules:
        for _label, paths in item["components"]:
            for path in paths:
                value = nested(path)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    parts.append(f"{path}: {_rub(float(value))}")
        for path in item["total"]:
            value = nested(path)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                parts.append(f"{path}: {_rub(float(value))}")

    limit = spec.deduction_limit
    if limit:
        value = nested(limit["path"])
        if value:
            parts.append(f"{limit['path']}: {value}")

    bank = spec.bank
    for role, path in bank.items():
        value = nested(path)
        if value:
            parts.append(f"{role}: {value}")

    body = "; ".join(p for p in parts if p)
    filler = (
        " Судебному приставу-исполнителю предписано произвести удержание из "
        "заработной платы должника. Руководствуясь статьёй 99 Федерального закона "
        "от 02.10.2007 N 229-ФЗ, "
    )
    text = body + filler
    if rng.random() < 0.3:
        text = text.upper()
    return text


def _rub(value: float) -> str:
    """Сумма в формате 1С: целая часть с пробелами, копейки через запятую."""
    return f"{value:,.2f}".replace(",", " ").replace(".", ",")


def load_ground_truth() -> Dict[str, List[Dict[str, object]]]:
    """Загружает эталонные наборы из data/ground_truth."""
    import json
    from pathlib import Path

    gt_dir = Path(__file__).resolve().parents[2] / "data" / "ground_truth"
    out: Dict[str, List[Dict[str, object]]] = {}
    for path in sorted(gt_dir.glob("*.json")):
        try:
            items = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(items, list):
            out[path.stem] = items
    return out


def expected_type_for(doc: Dict[str, object], default: str) -> str:
    """Тип документа, ожидаемый для данного эталона."""
    return default


def messy_names_for(plugin_id: str) -> List[str]:
    return list(MESSY_FILENAMES.get(plugin_id, []))


def build_ocr_variants(
    text: str,
    rates: Iterable[float] = (0.0, 0.01, 0.02, 0.05, 0.10),
    repeats: int = 5,
    seed: int = 20260928,
) -> Dict[float, List[str]]:
    """
    Строит варианты эталона с разной долей ошибок распознавания.

    :returns: {доля шума: [тексты вариантов]}
    """
    out: Dict[float, List[str]] = {}
    for rate in rates:
        variants = [
            inject_ocr_noise(text, rate, seed=seed + int(rate * 1000) + i)
            for i in range(repeats)
        ]
        out[rate] = variants
    return out


def build_multi_page_pdf(
    path: str,
    pages: int,
    key_lines: Optional[Sequence[str]] = None,
    key_page: Optional[int] = None,
) -> str:
    """
    Создаёт многостраничный PDF, реквизиты которого лежат на key_page.

    Нужен для проверки лимита страниц: если лимит 20, а реквизиты на 30-й
    странице, извлечение их не увидит.
    """
    fitz = _require_fitz()
    doc = fitz.open()
    lines = list(key_lines) if key_lines else ["ИНН должника 7707083893",
                                              "Итого к удержанию: 53500,00 руб."]
    key_page = key_page or pages
    for i in range(1, pages + 1):
        page = doc.new_page()
        if i == key_page:
            for line in lines:
                page.insert_text((72, 100), line)
        else:
            page.insert_text(
                (72, 100),
                f"Страница {i} из {pages}. Продолжение процессуального документа.",
            )
    doc.save(path)
    doc.close()
    return path


def _require_fitz():
    try:
        import fitz
        return fitz
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("PyMuPDF обязателен для генерации многостраничных PDF") from e

"""
Универсальный загрузчик текстовых и офисных документов (core/document_loader.py).
Поддерживает форматы:
1. Текстовые файлы: .txt, .md, .json, .csv, .log, .yaml, .yml, .xml, .html (UTF-8, CP1251, CP866, Latin-1).
2. Современные документы Word: .docx (через python-docx или встроенный zipfile + xml.etree парсер).
3. Устаревшие документы Word: .doc (DOCX-as-DOC, RTF-as-DOC и OLE2).
4. RTF документы: .rtf.
"""

import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Tuple, Union
from .utils import get_logger
from .io_utils import MAX_INPUT_BYTES, InputFileError

logger = get_logger("core.document_loader")

SUPPORTED_TEXT_EXTS = {".txt", ".md", ".json", ".csv", ".log", ".yaml", ".yml", ".xml", ".html", ".htm", ".rtf"}
SUPPORTED_WORD_EXTS = {".docx", ".doc"}

# Максимальный размер одного члена внутри zip-архива (docx) для защиты от zip-бомб (M-17)
MAX_ZIP_MEMBER_BYTES = MAX_INPUT_BYTES


def _clean_source_title(file_path: Union[str, Path]) -> str:
    """Извлечение читаемого названия документа из имени файла."""
    p = Path(file_path)
    title = p.stem.replace("_", " ").replace("-", " ")
    title = re.sub(r"\s+", " ", title).strip().title()
    return title or "Документ"


# P0.5: карта кодовых страниц RTF (\ansicpgNNNN) для декодирования \'hh-эскейпов.
_RTF_CODEPAGE_MAP = {
    1250: "cp1250", 1251: "cp1251", 1252: "cp1252", 1254: "cp1254",
    1255: "cp1255", 1256: "cp1256", 1257: "cp1257", 1258: "cp1258",
    874: "cp874", 932: "cp932", 936: "cp936", 949: "cp949", 950: "cp950",
    10000: "mac-roman",
}

# P0.5: destination-группы, чье содержимое не является текстом документа.
_RTF_SKIP_GROUPS = (
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "objdata",
    "themedata", "datastore", "listtable", "listoverridetable", "filetbl",
    "xmlnstbl", "rsidtbl", "latentstyles", "colorschememapping", "panose",
    "mmathPr", "shpinst", "nonshppict", "revtbl", "wgrffmtfilter", "fchars",
    "lchars", "ftnsep", "ftnsepc", "ftncn", "aftnsep", "aftnsepc", "aftncn",
)

_HEX2 = re.compile(r"[0-9a-fA-F]{2}")
_CTRL_WORD = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?")
_UNICODE_ESCAPE = re.compile(r"\\u(-?\d+)")


def _rtf_skip_group(raw: str, i: int, n: int) -> int:
    """Пропуск группы с балансом скобок (вложенность + экранированные \\{ \\})."""
    depth = 0
    j = i
    while j < n:
        ch = raw[j]
        if ch == "\\" and j + 1 < n and raw[j + 1] in "{}\\":
            j += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


def _strip_rtf_markup(raw: str) -> str:
    """
    Извлечение читаемого текста из RTF-разметки (M-14, P0.5).
    Поточный сканер вместо набора regex: обрабатывает \'hh-эскейпы через
    кодовую страницу из \\ansicpg (по умолчанию 1252 по спецификации RTF),
    \\uNNNN-эскейпы юникода (в т.ч. отрицательные), \\ucN-замещающие байты,
    destination-группы ({\\*...}, {\\fonttbl...}) с пропуском по глубине и
    экранированные скобки \\\\, \\{, \\}. Ранее кириллица в \'hh декодировалась
    как Latin-1 (mojibake), а \\uNNNN уничтожалась вместе с контрол-вордами.
    """
    m = re.search(r"\\ansicpg(\d+)", raw[:2048])
    encoding = _RTF_CODEPAGE_MAP.get(int(m.group(1)), "cp1252") if m else "cp1252"

    out: list = []
    hexbuf: list = []
    i, n = 0, len(raw)
    uc = 1  # число замещающих байтов после \uNNNN (управляется \ucN)

    def _flush_hex() -> None:
        if hexbuf:
            try:
                out.append(bytes(hexbuf).decode(encoding))
            except (UnicodeDecodeError, LookupError):
                out.append(bytes(hexbuf).decode("latin-1", errors="replace"))
            hexbuf.clear()

    while i < n:
        ch = raw[i]
        if ch == "{":
            head = raw[i : i + 300]
            if head.startswith("{\\*"):
                i = _rtf_skip_group(raw, i, n)
                continue
            mw = re.match(r"\{\\([a-zA-Z]+)", head)
            if mw and mw.group(1).lower() in _RTF_SKIP_GROUPS:
                i = _rtf_skip_group(raw, i, n)
                continue
            i += 1  # групповая скобка — не текст
            continue
        if ch == "}":
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            nxt = raw[i + 1]
            if nxt == "'":  # \'hh — байт в кодовой странице документа
                h = raw[i + 2 : i + 4]
                if len(h) == 2 and _HEX2.fullmatch(h):
                    hexbuf.append(int(h, 16))
                    i += 4
                    continue
                i += 2  # поврежденный эскейп
                continue
            if nxt == "u":
                mu = _UNICODE_ESCAPE.match(raw, i)
                if mu:
                    _flush_hex()
                    code = int(mu.group(1))
                    if code < 0:
                        code += 65536
                    out.append(chr(code))
                    i = mu.end()
                    # замещающие байты: '?' или \'hh (по \ucN, обычно 1)
                    for _ in range(uc):
                        if i < n and raw[i] == "?":
                            i += 1
                        elif i + 3 < n and raw[i : i + 2] == "\\'" and _HEX2.fullmatch(raw[i + 2 : i + 4]):
                            i += 4
                        else:
                            break
                    continue
            if nxt in "\\{}":  # экранированные литералы
                _flush_hex()
                out.append(nxt)
                i += 2
                continue
            if nxt == "~":  # неразрывный пробел
                _flush_hex()
                out.append(" ")
                i += 2
                continue
            if nxt in "*-_":  # мягкий дефис, индексы и т.п.
                i += 2
                continue
            mw = _CTRL_WORD.match(raw, i)
            if mw:
                word = mw.group(1).lower()
                if word == "uc":
                    try:
                        uc = abs(int(mw.group(2) or 1))
                    except ValueError:
                        uc = 1
                    i = mw.end()
                    continue
                _flush_hex()
                if word in ("par", "line", "page", "sect", "sectnum", "nestrow"):
                    out.append("\n")
                elif word in ("tab", "cell", "nestcell", "row"):
                    out.append(" ")
                i = mw.end()
                continue
            i += 2  # неизвестный эскейп — пропустить
            continue
        # обычный символ
        _flush_hex()
        out.append(ch)
        i += 1

    _flush_hex()
    text = "".join(out)
    # Нормализуем пробелы и пустые строки
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _detect_encoding_and_read(file_path: Path) -> str:
    """
    Чтение текстового файла с детекцией BOM и UTF-16 без BOM (M-16),
    затем перебором популярных кодировок.
    """
    raw_bytes = file_path.read_bytes()

    # Явные BOM: UTF-8, UTF-16 LE/BE, UTF-32
    if raw_bytes.startswith(b"\xef\xbb\xbf"):
        return raw_bytes.decode("utf-8-sig", errors="replace")
    if raw_bytes.startswith(b"\xff\xfe\x00\x00") or raw_bytes.startswith(b"\x00\x00\xfe\xff"):
        return raw_bytes.decode("utf-32", errors="replace")
    if raw_bytes.startswith(b"\xff\xfe"):
        return raw_bytes.decode("utf-16-le", errors="replace")
    if raw_bytes.startswith(b"\xfe\xff"):
        return raw_bytes.decode("utf-16-be", errors="replace")

    # UTF-16 без BOM: чередование «высоких» почти константных байт (0x00/0x04/0x2x)
    # с вариативными «низкими» байтами (M-16). Кириллица в UTF-16 = U+04xx,
    # поэтому нулевых байт может не быть вовсе — проверяется структура пар.
    sample = raw_bytes[:4096]
    if len(sample) >= 16:
        even, odd = sample[0::2], sample[1::2]
        for hi, lo, enc in ((odd, even, "utf-16-le"), (even, odd, "utf-16-be")):
            if (
                len(sample) % 2 == 0
                and len(set(hi)) <= 2
                and max(hi) < 0x20
                and len(set(lo)) > 2
            ):
                decoded = raw_bytes.decode(enc, errors="replace")
                if decoded.strip():
                    return decoded
        if sample.count(0) > len(sample) * 0.3:
            logger.warning(f"Файл {file_path.name} содержит аномально много нулевых байт — возможен mojibake.")

    for enc in ("utf-8", "cp1251", "windows-1251", "cp866", "latin-1"):
        try:
            content = raw_bytes.decode(enc)
            if content.strip():
                return content
        except (UnicodeDecodeError, LookupError) as e:
            logger.debug(f"Декодирование {file_path.name} как {enc} не удалось: {e}")
            continue

    return raw_bytes.decode("utf-8", errors="ignore")


def _extract_text_from_plain(file_path: Path) -> str:
    """Чтение текстовых файлов с BOM-детекцией и перебором кодировок."""
    content = _detect_encoding_and_read(file_path)
    if file_path.suffix.lower() == ".rtf" and content.lstrip().startswith("{\\rtf"):
        # M-14: RTF парсится как разметка, а не отдается сырым в VLM
        stripped = _strip_rtf_markup(content)
        if stripped:
            return stripped
        logger.warning(f"RTF-разметка файла {file_path.name} не дала текста — передается как есть.")
    return content


def _extract_text_from_docx(file_path: Path) -> str:
    """
    Извлечение текста из .docx документа.
    1. Попытка через python-docx (если установлен).
    2. Fallback на zipfile + XML etree парсер.
    """
    try:
        import docx
        doc = docx.Document(str(file_path))
        full_text = []
        for para in doc.paragraphs:
            if para.text.strip():
                full_text.append(para.text.strip())
        for table in doc.tables:
            for row in table.rows:
                row_text = " | ".join(cell.text.strip() for cell in row.cells if cell.text.strip())
                if row_text:
                    full_text.append(row_text)
        if full_text:
            return "\n\n".join(full_text)
    except ImportError as e:
        logger.debug(f"python-docx недоступен, используется XML-парсер: {e}")
    except Exception as e:
        logger.warning(f"python-docx не смог прочитать {file_path.name} ({e}), переключаемся на XML-парсер.")

    # Встроенный парсинг word/document.xml (с лимитом размера члена архива, M-17)
    try:
        with zipfile.ZipFile(file_path, "r") as z:
            if "word/document.xml" not in z.namelist():
                raise ValueError("Не найден word/document.xml в архиве docx.")
            info = z.getinfo("word/document.xml")
            if info.file_size > MAX_ZIP_MEMBER_BYTES:
                raise InputFileError(
                    f"Член архива docx превышает лимит ({info.file_size} > {MAX_ZIP_MEMBER_BYTES} байт): {file_path.name}"
                )
            xml_bytes = z.read("word/document.xml")
            tree = ET.fromstring(xml_bytes)

            ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            paragraphs = []
            for p in tree.iter(f"{{{ns['w']}}}p"):
                texts = [node.text for node in p.iter(f"{{{ns['w']}}}t") if node.text]
                p_str = "".join(texts).strip()
                if p_str:
                    paragraphs.append(p_str)

            if paragraphs:
                return "\n\n".join(paragraphs)
    except Exception as e:
        logger.warning(f"Ошибка XML-парсинга docx ({e}), резервное извлечение...")

    return _extract_text_from_plain(file_path)


def _extract_text_from_doc(file_path: Path) -> str:
    """
    Извлечение текста из .doc файлов (OLE2, RTF-as-doc или переименованный docx).
    M-15: настоящий OLE2-бинарник не скармливается VLM как mojibake —
    при неудаче парсеров возвращается пустой текст с предупреждением.
    """
    with open(file_path, "rb") as f:
        header = f.read(8)

    # DOCX контейнер (PK\x03\x04)
    if header.startswith(b"PK\x03\x04"):
        return _extract_text_from_docx(file_path)

    # RTF заголовок ({\rtf)
    if header.startswith(b"{\\rtf"):
        return _extract_text_from_plain(file_path)

    # Попытка через docx2txt (умеет частично OLE2)
    try:
        import docx2txt
        result = docx2txt.process(str(file_path))
        if result and result.strip():
            return result
        logger.warning(f"docx2txt вернул пустой результат для {file_path.name}.")
    except ImportError as e:
        logger.debug(f"docx2txt недоступен: {e}")
    except Exception as e:
        logger.warning(f"docx2txt не смог прочитать {file_path.name}: {e}")

    # OLE2-бинарник (D0 CF 11 E0): без OLE2-парсера не читаем мусор
    if header.startswith(b"\xd0\xcf\x11\xe0"):
        logger.warning(
            f"Файл {file_path.name} — бинарный OLE2 .doc без доступного парсера; "
            f"текст не извлечен (требуется конвертация в .docx)."
        )
        return ""

    logger.warning(f"Неизвестный формат .doc ({file_path.name}); попытка чтения как текст.")
    return _extract_text_from_plain(file_path)


def load_document(file_path: Union[str, Path]) -> Tuple[str, str]:
    """
    Универсальная загрузка любого текстового/офисного документа.
    Возвращает кортеж: (document_title, extracted_text).
    M-17: файлы свыше MAX_INPUT_BYTES отклоняются.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Файл не найден: {file_path}")

    size = path.stat().st_size
    if size > MAX_INPUT_BYTES:
        raise InputFileError(f"Входной файл превышает лимит размера ({size} > {MAX_INPUT_BYTES} байт): {file_path}")

    ext = path.suffix.lower()
    title = _clean_source_title(path)

    if ext == ".docx":
        text = _extract_text_from_docx(path)
    elif ext == ".doc":
        text = _extract_text_from_doc(path)
    elif ext in SUPPORTED_TEXT_EXTS:
        text = _extract_text_from_plain(path)
    else:
        text = _extract_text_from_plain(path)

    return title, text

# -*- coding: utf-8 -*-
"""
Единый фасад платформы распознавания юридических документов (LegalDocPlatformFacade).
Реализует:
1. Fast-Path Router (CoT VLM классификатор с Dual-Zone нарезкой шапки)
2. Динамическое разделение потоков (Specialized Extraction Branches)
3. Гарантированный захват денежных сумм и резервный Financial Regex Scanner
4. Автономные Guardrails (autonomous.json)
5. Эталонный бенчмаркинг (Ground Truth)
6. Изоляцию ошибок и сквозную телеметрию (RateLimiter, TokenTracker, Cache)
"""

import os
import json
import time
import re
import difflib
from typing import Dict, Any, List, Optional, Tuple, Union
from dotenv import load_dotenv

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.abspath(os.path.join(SCRIPT_DIR, ".."))
PROJECT_ROOT = os.path.abspath(os.path.join(SRC_DIR, ".."))
ROOT_DIR = PROJECT_ROOT

# Загрузка переменных окружения (C-13): приоритет cwd, затем корень проекта
load_dotenv(os.path.join(os.getcwd(), ".env"))
load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

def get_ground_truth_path(gt_filename: Optional[str]) -> Optional[str]:
    """Разрешает путь к файлу эталонов (ground truth)."""
    if not gt_filename:
        return None
    env_dir = os.getenv("SCANREADER_GROUND_TRUTH_DIR")
    if env_dir:
        cand = os.path.join(env_dir, gt_filename)
        if os.path.exists(cand):
            return cand
    cwd_cand = os.path.join(os.getcwd(), "data", "ground_truth", gt_filename)
    if os.path.exists(cwd_cand):
        return cwd_cand
    root_cand = os.path.join(PROJECT_ROOT, "data", "ground_truth", gt_filename)
    if os.path.exists(root_cand):
        return root_cand
    return None

from pydantic import BaseModel, Field, field_validator

# H-16: единый набор относительных импортов без маскирующих ImportError-fallback
from .core.utils import _safe_parse_json, get_logger, coerce_to_str
from .core.token_tracker import TokenUsageTracker
from .core.rate_limiter import RateLimiter
from .core.cache import LLMResponseCache
from .core.finance_parser import parse_russian_currency, extract_amounts_from_text
from .core.metrics_evaluator import (
    evaluate_dataset,
    generate_run_summary,
    export_metrics_json,
    append_to_metrics_history,
    export_run_summary_markdown,
    export_run_summary_excel,
    validate_ip_number_format
)
from .core.json_exporter import (
    save_single_document_json,
    export_consolidated_registries,
    save_checkpoint,
    load_checkpoint,
    clean_checkpoint
)
from .type_registry import get_registry, UNKNOWN_CATEGORY
from .file_processor import FileProcessor, scan_directory_for_documents, SUPPORTED_IMAGE_EXTS
from .core.document_loader import load_document, SUPPORTED_TEXT_EXTS, SUPPORTED_WORD_EXTS
from .verifier.auditor import ZeroTrustAuditor
from .verifier.chronology import parse_flexible_date
from .verifier.checksums import validate_inn

logger = get_logger("facade")


def _get_version() -> str:
    """Единая версия платформы из __version__ пакета (M-01)."""
    try:
        from . import __version__
        return __version__
    except Exception:
        return "0.9.0"


class UniversalDocumentDoc(BaseModel):
    """Базовая универсальная Pydantic-схема для неопределенных или неподдерживаемых типов документов."""
    file_name: str = Field(default="", description="Имя файла документа")
    doc_type: str = Field(default="Неопределенный документ", description="Тип документа")
    doc_date: str = Field(default="", description="Дата документа")
    doc_number: str = Field(default="", description="Номер документа")
    court_or_authority: str = Field(default="", description="Суд или орган")
    claimant_name: str = Field(default="", description="Взыскатель/истец")
    debtor_name: str = Field(default="", description="Должник/ответчик")
    claim_subject: str = Field(default="", description="Предмет требования")
    total_rub: Optional[float] = Field(default=None, description="Сумма в рублях")
    notes: str = Field(default="Требуется ручная проверка типа документа", description="Примечания")

    @field_validator('total_rub', mode='before')
    @classmethod
    def clean_amounts(cls, v: Any) -> Optional[float]:
        return parse_russian_currency(v)

    @field_validator(
        'file_name', 'doc_type', 'doc_date', 'doc_number', 'court_or_authority',
        'claimant_name', 'debtor_name', 'claim_subject', 'notes',
        mode='before'
    )
    @classmethod
    def clean_strings(cls, v: Any) -> str:
        return coerce_to_str(v)



def _glob_parts_match(pattern: str, text_lower: str) -> bool:
    """
    Сопоставление path_pattern по glob-семантике (H-07).
    '*' означает любую последовательность символов: литеральные части паттерна
    должны присутствовать в тексте в том же порядке.
    Пример: '*приказ*прием*' требует 'приказ' ... 'прием' (в порядке следования).
    Короткие одиночные токены (<= 4 символов) проверяются как целые слова,
    чтобы избежать ложных срабатываний подстрокой.
    """
    parts = [p.lower() for p in pattern.split("*") if p]
    if not parts:
        return False

    if len(parts) == 1 and len(parts[0]) <= 4:
        word = re.escape(parts[0])
        rgx = r'(?<![а-яёa-z0-9])' + word + r'(?![а-яёa-z0-9])'
        return re.search(rgx, text_lower) is not None

    pos = 0
    for part in parts:
        idx = text_lower.find(part, pos)
        if idx == -1:
            return False
        pos = idx + len(part)
    return True


class LegalDocPlatformFacade:
    """
    Главный управляющий фасад конвейера распознавания и анализа судебных документов.
    """

    def __init__(
        self,
        base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        model_name: Optional[str] = None,
        enable_cache: bool = True,
        results_dir: Optional[str] = None
    ):
        self.base_url: str = base_url or os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1") or "http://localhost:11434/v1"
        self.api_key: str = api_key or os.getenv("OPENAI_API_KEY", "dummy_local_key") or "dummy_local_key"
        self.model_name: str = model_name or os.getenv("AI_MODEL_NAME", "qwen2.5-vl:7b") or "qwen2.5-vl:7b"

        self.registry = get_registry()
        self.processor = FileProcessor()
        self.tracker = TokenUsageTracker.get_tracker()
        self.rate_limiter = RateLimiter.get_limiter()
        self.cache = LLMResponseCache(enabled=enable_cache)

        self.results_dir: str = ""
        if results_dir:
            self.results_dir = results_dir
        else:
            env_out = os.getenv("SCANREADER_OUTPUT_DIR")
            if env_out:
                self.results_dir = env_out
            else:
                cwd_legacy = os.path.join(os.getcwd(), "Результаты")
                cwd_output = os.path.join(os.getcwd(), "output")
                root_legacy = os.path.join(PROJECT_ROOT, "Результаты")
                if os.path.exists(cwd_legacy):
                    self.results_dir = cwd_legacy
                elif os.path.exists(root_legacy) and not os.path.exists(cwd_output):
                    self.results_dir = root_legacy
                else:
                    self.results_dir = cwd_output
        os.makedirs(self.results_dir, exist_ok=True)

        self.auditor = ZeroTrustAuditor()
        self._client: Optional[Any] = None

    def _extract_raw_text_for_audit(self, file_path: str) -> Optional[str]:
        """Безопасное извлечение текстового слоя (DOCX, TXT, PDF text layer) для кросс-модальной проверки."""
        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext in SUPPORTED_TEXT_EXTS or ext in SUPPORTED_WORD_EXTS:
                _, text = load_document(file_path)
                return text
            elif ext == ".pdf":
                try:
                    import fitz
                    doc = fitz.open(file_path)
                    text = "\n".join(page.get_text() for page in doc)
                    doc.close()
                    return text if text.strip() else None
                except Exception:
                    return None
        except Exception:
            return None
        return None

    def _transcribe_scan_for_audit(self, file_path: str) -> Optional[str]:
        """
        S-13: второй VLM-проход «точная транскрипция» для сканов без текстового слоя.
        Результат используется как эталон в кросс-модальном гейте: сверка ФИО, УИН,
        номеров ИП/ИЛ и других реквизитов экстракции против буквальной транскрипции
        ловит ошибки распознавания, невидимые для детерминированных проверок.
        Управление: SCANREADER_OCR_CROSSCHECK (по умолчанию включен).
        """
        if os.getenv("SCANREADER_OCR_CROSSCHECK", "true").lower() not in ("true", "1", "yes"):
            return None

        client = self._get_client()
        if not client:
            return None

        try:
            mode, inputs = self.processor.prepare_document_inputs(file_path, max_pages=2)
            if mode != "vision" or not isinstance(inputs, list) or not inputs:
                return None

            user_content: List[Dict[str, Any]] = [{
                "type": "text",
                "text": (
                    "Транскрибируй ТОЧНО весь видимый текст документа, включая ВСЕ цифры "
                    "(номера, счета, УИН, ИНН, БИК), ФИО и даты. Пиши точно как напечатано, "
                    "без исправлений, без пропусков и без комментариев. Выведи только текст."
                ),
            }]
            for uri in inputs:
                user_content.append({"type": "image_url", "image_url": {"url": uri}})

            base_name = os.path.basename(file_path)
            start_t = time.perf_counter()
            with self.rate_limiter:
                resp = client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": "Ты — точный OCR-транскрайбер юридических документов."},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=0.0,
                )
            latency = time.perf_counter() - start_t
            usage = resp.usage
            self.tracker.record_call(
                model=self.model_name,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                latency_sec=latency,
                is_cache_hit=False,
                stage_name="ocr_transcribe",
                doc_name=base_name,
            )
            text = (resp.choices[0].message.content or "").strip()
            return text if len(text) >= 50 else None
        except Exception as e:
            logger.debug(f"OCR-транскрипция для сверки не выполнена ('{file_path}'): {e}")
            return None

    extract_raw_text_for_audit = _extract_raw_text_for_audit

    def _get_client(self):
        """Ленивая безопасная инициализация OpenAI-клиента."""
        if self._client is None:
            try:
                from openai import OpenAI
                self._client = OpenAI(
                    base_url=self.base_url,
                    api_key=self.api_key or "dummy_local_key",
                    timeout=float(os.getenv("VLM_TIMEOUT", "600"))
                )
            except Exception as e:
                logger.warning(f"Не удалось инициализировать OpenAI клиент: {e}")
                return None
        return self._client

    # =========================================================================
    # 1. FAST-PATH ROUTER С DUAL-ZONE НАБЛЮДЕНИЕМ И CHAIN-OF-THOUGHT
    # =========================================================================
    def classify_via_vlm(self, file_path: str) -> Tuple[str, float, str, Dict[str, Any]]:
        """
        Мультимодальный Fast-Path Router первой страницы с Dual-Zone нарезкой (Шапка крупно + Вся страница)
        и пошаговым Chain-of-Thought анализом юридических якорей.
        """
        client = self._get_client()
        if not client:
            return UNKNOWN_CATEGORY, 0.0, "vlm_unavailable", {}

        try:
            mode, inputs = self.processor.prepare_dual_zone_inputs(file_path)
            base_name = os.path.basename(file_path)

            # Динамическое формирование категорий из реестра активных плагинов
            plugins_desc = []
            valid_types = []
            for idx, pl in enumerate(self.registry.enabled_by_tie_priority(), 1):
                valid_types.append(pl.id)
                kws = pl.manifest.get("classifier", {}).get("keywords", [])
                kw_str = ", ".join(kws[:6]) if kws else "юридические маркеры"
                plugins_desc.append(f"{idx}. \"{pl.id}\" ({pl.title}):\n   • Ключевые маркеры и заголовок: {kw_str}")

            valid_types.append("unknown")
            plugins_desc.append(f"{len(plugins_desc) + 1}. \"unknown\": Если документ не относится ни к одной из перечисленных категорий.")
            categories_block = "\n\n".join(plugins_desc)
            types_options = " | ".join(valid_types)

            sys_prompt = (
                "Ты — экспертный юридический классификатор-маршрутизатор (Document Router).\n"
                "Твоя задача — точно определить категорию документа по его визуальным якорям, шапке и заголовку.\n\n"
                f"ПОДДЕРЖИВАЕМЫЕ КАТЕГОРИИ ДОКУМЕНТОВ:\n{categories_block}\n\n"
                "ИНСТРУКЦИЯ ПО АНАЛИЗУ (Chain-of-Thought):\n"
                "1. Определи автора/орган или стороны документа (Суд, Пристав ФССП, Контрагенты, Работодатель).\n"
                "2. Найди визуальные якоря (Герб, серия бланка, штампы канцелярии, реквизиты, таблицы).\n"
                "3. Прочитай точный заголовок документа в верхней зоне.\n"
                "4. Сделай итоговый вывод о категории.\n\n"
                "Ответь СТРОГО валидным JSON:\n"
                "{\n"
                '  "issuing_authority": "кто издал документ / стороны",\n'
                '  "visual_anchors": ["найденные визуальные маркеры"],\n'
                '  "title_text": "заголовок документа",\n'
                f'  "doc_type": "{types_options}",\n'
                '  "confidence": 0.95,\n'
                '  "reason": "краткое объяснение вывода"\n'
                "}"
            )

            user_content: List[Dict[str, Any]] = []

            if mode == "vision_dual_zone" and isinstance(inputs, dict):
                user_content.append({"type": "text", "text": f"Документ: '{base_name}'\n🔍 ФРАГМЕНТ 1: Верхняя зона документа крупно (Шапка, герб, штампы, адресат):"})
                user_content.append({"type": "image_url", "image_url": {"url": inputs["header_uri"]}})
                user_content.append({"type": "text", "text": "🔍 ФРАГМЕНТ 2: Вся страница целиком (Общий визуальный контекст):"})
                user_content.append({"type": "image_url", "image_url": {"url": inputs["full_uri"]}})
            elif mode == "vision" and isinstance(inputs, list):
                user_content.append({"type": "text", "text": f"Документ: '{base_name}'"})
                user_content.append({"type": "image_url", "image_url": {"url": inputs[0]}})
            else:
                text_snippet = str(inputs)[:3000]
                user_content.append({"type": "text", "text": f"Документ: '{base_name}'\nТекст первой страницы:\n\n{text_snippet}"})

            start_t = time.perf_counter()
            with self.rate_limiter:
                resp = client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": sys_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=0.0
                )
            dur = round(time.perf_counter() - start_t, 2)

            reply = resp.choices[0].message.content or ""
            parsed = _safe_parse_json(reply)
            dt = parsed.get("doc_type", "").strip()
            conf = float(parsed.get("confidence", 0.8))

            logger.info(f"  [Router CoT {dur}s]: '{dt}' (conf: {conf}) -> {parsed.get('reason', '')}")

            if dt in self.registry.ids():
                return dt, conf, "vlm_dual_zone_router", parsed

        except Exception as e:
            logger.warning(f"Router не сработал для '{file_path}': {e}")

        return UNKNOWN_CATEGORY, 0.0, "unknown", {}

    def classify_document(self, file_path: str, use_vlm_fallback: bool = True) -> Tuple[str, float, str]:
        """
        Комплексная классификация документа:
        1. Быстрый эвристический анализ путей, имен и транслитерации.
        2. Анализ текстовых маркеров (для текстовых/DOCX документов).
        3. Мультимодальный Fast-Path Router (Dual-Zone CoT) для сканов и изображений.
        """
        norm_path = file_path.replace("\\", "/").lower()
        file_name = os.path.basename(file_path).lower()

        # 1. Эвристический проход по путям и именам файлов (glob-семантика, H-07)
        for plugin in self.registry.enabled_by_tie_priority():
            rules = plugin.classifier_rules
            for pattern in rules.get("path_patterns", []):
                if not isinstance(pattern, str) or not pattern.strip():
                    continue
                if _glob_parts_match(pattern, norm_path) or _glob_parts_match(pattern, file_name):
                    return plugin.id, 0.95, "heuristic_path"

            for kw in plugin.manifest.get("classifier", {}).get("keywords", []):
                kw_clean = kw.strip().lower()
                if kw_clean:
                    pat = r'(?<![а-яёa-z0-9])' + re.escape(kw_clean) + r'(?![а-яёa-z0-9])'
                    if re.search(pat, norm_path) or re.search(pat, file_name):
                        return plugin.id, 0.90, "heuristic_keyword"

        # 2. Проверка текста для текстовых/DOCX файлов
        try:
            mode, content = self.processor.prepare_document_inputs(file_path, max_pages=1)
            if mode == "text" and isinstance(content, str) and content.strip():
                text_lower = content.lower()
                for plugin in self.registry.enabled_by_tie_priority():
                    for marker in plugin.classifier_rules.get("text_markers", []):
                        if marker.lower() in text_lower:
                            return plugin.id, 0.92, "text_content_marker"
        except Exception as e:
            logger.debug(f"Текстовые маркеры классификации не проверены для '{file_path}': {e}")

        # 3. Мультимодальный Dual-Zone CoT Router
        if use_vlm_fallback:
            dt, conf, method, _ = self.classify_via_vlm(file_path)
            if dt != UNKNOWN_CATEGORY:
                return dt, conf, method

        return UNKNOWN_CATEGORY, 0.0, "unknown"

    # =========================================================================
    # 2. СПЕЦИАЛИЗИРОВАННАЯ ЭКСТРАКЦИЯ (DYNAMIC BRANCHING + FINANCIAL RECOVERY)
    # =========================================================================
    def extract_document_data(
        self,
        file_path: str,
        doc_type: Optional[str] = None,
        max_pages: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Извлекает структурированные данные через специализированный поток плагина (Specialized Branch)
        с гарантированным финансовым пост-процессингом и Regex-восстановлением пропущенных сумм.
        """
        if not doc_type or doc_type == UNKNOWN_CATEGORY:
            doc_type, _, _ = self.classify_document(file_path)

        plugin = self.registry.get(doc_type)
        base_name = os.path.basename(file_path)

        # Fallback для неизвестных категорий (Zero Crashes)
        if plugin is None:
            logger.warning(f"⚠️ Тип документа '{doc_type}' не определен. Выполняется универсальная экстракция.")
            system_prompt = (
                "Ты — юридический аналитик. Проанализируй документ и извлеки базовые реквизиты строго в JSON:\n"
                "{\n"
                '  "doc_type": "Неопределенный документ",\n'
                '  "doc_date": "дата",\n'
                '  "doc_number": "номер",\n'
                '  "court_or_authority": "суд или орган",\n'
                '  "claimant_name": "взыскатель/истец",\n'
                '  "debtor_name": "должник/ответчик",\n'
                '  "claim_subject": "предмет требования",\n'
                '  "total_rub": null,\n'
                '  "notes": "Требуется ручная проверка типа документа"\n'
                "}"
            )
        else:
            system_prompt = plugin.prompt_text

        mode, inputs = self.processor.prepare_document_inputs(file_path, max_pages=max_pages)

        # Вычисление ключа кэша с защитой от коллизий и отслеживанием изменений файла
        file_stat = ""
        try:
            st = os.stat(file_path)
            file_stat = f"::{st.st_size}::{st.st_mtime}"
        except Exception as e:
            logger.debug(f"Не удалось получить статистику файла '{file_path}' для ключа кэша: {e}")
        import hashlib
        inputs_digest = hashlib.sha256(repr(inputs).encode("utf-8", errors="replace")).hexdigest()
        cache_key_content = f"{mode}::{base_name}{file_stat}::{inputs_digest}"
        cache_key = self.cache.compute_key(system_prompt, cache_key_content, self.model_name)

        # Проверка кэша
        cached_resp = self.cache.get(cache_key)
        if cached_resp:
            parsed = _safe_parse_json(cached_resp)
            if parsed:
                self.tracker.record_call(
                    model=self.model_name,
                    is_cache_hit=True,
                    stage_name=f"extract_{doc_type}",
                    doc_name=base_name
                )
                parsed["file_name"] = base_name
                return parsed

        # Вызов специализированной модели через RateLimiter
        self.tracker.set_stage(f"extract_{doc_type}")
        user_message_content: List[Dict[str, Any]] = []

        if mode == "vision" and isinstance(inputs, list):
            user_message_content.append({"type": "text", "text": f"Проанализируй документ '{base_name}' и заполни JSON по структуре (особое внимание удели суммам):"})
            for uri in inputs:
                user_message_content.append({
                    "type": "image_url",
                    "image_url": {"url": uri}
                })
        else:
            user_message_content.append({
                "type": "text",
                "text": f"Проанализируй текст документа '{base_name}' и заполни JSON по структуре (особое внимание удели суммам):\n\n{inputs}"
            })

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message_content}
        ]

        client = self._get_client()
        if not client:
            logger.error("LLM клиент недоступен. Возврат пустой структуры.")
            return {"file_name": base_name, "error": "LLM client unavailable", "status": "FAILED"}

        start_time = time.perf_counter()
        with self.rate_limiter:
            response = client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=0.0
            )

        latency_sec = time.perf_counter() - start_time
        reply_text = response.choices[0].message.content or ""
        usage = response.usage

        prompt_tokens = usage.prompt_tokens if usage else 0
        comp_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else (prompt_tokens + comp_tokens)

        # Регистрация токенов
        self.tracker.record_call(
            model=self.model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=comp_tokens,
            total_tokens=total_tokens,
            latency_sec=latency_sec,
            is_cache_hit=False,
            stage_name=f"extract_{doc_type}",
            doc_name=base_name
        )

        parsed_data = _safe_parse_json(reply_text)
        if not parsed_data:
            logger.warning(f"Не удалось извлечь JSON из ответа модели для '{base_name}'. Сохраняем сырой текст.")
            parsed_data = {"raw_reply": reply_text}

        parsed_data["file_name"] = base_name

        # ---------------------------------------------------------------------
        # 💰 ФИНАНСОВЫЙ ВОССТАНОВИТЕЛЬ (FINANCIAL RECOVERY FALLBACK)
        # ---------------------------------------------------------------------
        # Если в извлеченных данных суммы не заполнены, сканируем текст ответа и текст документа
        fin_obj = parsed_data.get("finances", {})
        if not isinstance(fin_obj, dict):
            fin_obj = {}
            parsed_data["finances"] = fin_obj

        # Проверка ключевых сумм
        cur_total = fin_obj.get("total_rub") or fin_obj.get("total_deduction_rub") or fin_obj.get("debt_amount_rub")
        if cur_total is None or cur_total == 0.0 or str(cur_total).strip() in ("", "None", "null"):
            text_to_scan = reply_text
            if mode == "text" and isinstance(inputs, str):
                text_to_scan += "\n" + inputs
            
            regex_amounts = extract_amounts_from_text(text_to_scan)
            if regex_amounts.get("total_rub"):
                logger.info(f"💰 Резервный Regex-сканер восстановил сумму для '{base_name}': {regex_amounts['total_rub']} руб.")
                if "total_rub" in fin_obj or doc_type in ("executive_documents", "enforcement_orders"):
                    fin_obj["total_rub"] = regex_amounts["total_rub"]
                if "total_deduction_rub" in fin_obj or doc_type == "salary_deductions":
                    fin_obj["total_deduction_rub"] = regex_amounts["total_rub"]
                if "debt_amount_rub" in fin_obj and not fin_obj.get("debt_amount_rub"):
                    fin_obj["debt_amount_rub"] = regex_amounts.get("main_debt_rub") or regex_amounts["total_rub"]
                if "main_debt_rub" in fin_obj and not fin_obj.get("main_debt_rub"):
                    fin_obj["main_debt_rub"] = regex_amounts.get("main_debt_rub") or regex_amounts["total_rub"]
                if "court_fee_rub" in fin_obj and not fin_obj.get("court_fee_rub"):
                    fin_obj["court_fee_rub"] = regex_amounts.get("court_fee_rub")
                if "court_costs_rub" in fin_obj and not fin_obj.get("court_costs_rub"):
                    fin_obj["court_costs_rub"] = regex_amounts.get("court_fee_rub")

        # Валидация по схеме Pydantic (с пре-валидаторами очистки сумм и нормализации строк)
        schema_cls = plugin.schema_cls if plugin else UniversalDocumentDoc
        try:
            schema_instance = schema_cls.model_validate(parsed_data)
            validated_dict = schema_instance.model_dump()
        except Exception as e:
            logger.warning(f"Частичное несоответствие схеме ({e}), сохраняем исходные поля.")
            if isinstance(parsed_data, dict):
                validated_dict = dict(parsed_data)
            else:
                validated_dict = {"raw_output": str(parsed_data)}
            validated_dict["schema_validated"] = False
            validated_dict["_schema_validation_error"] = str(e)

        # Сохранение в кэш
        self.cache.set(cache_key, json.dumps(validated_dict, ensure_ascii=False))
        return validated_dict

    # =========================================================================
    # 3. АВТОНОМНЫЕ ПРОВЕРКИ (GUARDRAILS)
    # =========================================================================
    def validate_document(self, data: Dict[str, Any], doc_type: str) -> Dict[str, Any]:
        """
        Проверяет извлеченные данные по правилам autonomous.json плагина.
        """
        plugin = self.registry.get(doc_type)
        if not plugin or doc_type == UNKNOWN_CATEGORY:
            return {
                "passed": False,
                "score": 0.0,
                "issues": [{"field": "doc_type", "severity": "WARNING", "message": "Тип документа требует ручной верификации"}]
            }

        rules = plugin.autonomous_config.get("fields", [])
        issues = []

        def _get_nested(d: Dict[str, Any], path: str) -> Any:
            parts = path.split(".")
            curr: Any = d
            for p in parts:
                if isinstance(curr, dict):
                    curr = curr.get(p)
                else:
                    return None
            return curr

        passed_count = 0
        total_rules = len(rules)
        known_rules = {"not_empty", "valid_date_format", "positive_number_or_percentage",
                       "positive_number", "valid_inn", "ip_number_format", "ip_number_format_optional"}

        for r in rules:
            if not isinstance(r, dict):
                continue
            field_path = r.get("field", "")
            rule_type = r.get("rule", "")
            severity = r.get("severity", "MEDIUM")
            msg = r.get("message", f"Ошибка проверки {field_path}")

            val = _get_nested(data, field_path)
            is_valid = True

            if rule_type == "not_empty":
                is_valid = bool(val and str(val).strip())
            elif rule_type == "valid_date_format":
                # M-02: реальная проверка распознаваемой даты, а не длины строки
                is_valid = bool(val) and parse_flexible_date(str(val)) is not None
            elif rule_type == "positive_number_or_percentage":
                # M-03: проверка знака и числового/процентного формата
                if val is None:
                    is_valid = False
                else:
                    s = str(val).strip().replace(" ", "").replace(",", ".")
                    if s.endswith("%"):
                        s = s[:-1]
                    try:
                        is_valid = float(s) > 0
                    except (ValueError, TypeError):
                        is_valid = False
            elif rule_type == "positive_number":
                try:
                    is_valid = bool(val is not None and float(val) > 0)
                except (ValueError, TypeError):
                    is_valid = False
            elif rule_type == "valid_inn":
                if val:
                    ok_inn, _msg_inn = validate_inn(str(val))
                    is_valid = ok_inn
                else:
                    is_valid = False
            elif rule_type == "ip_number_format":
                # Формат номера ИП: NNNNN/NN/NNNN(N)-ИП (слэши обязательны, номер обязателен)
                if not val:
                    is_valid = False
                else:
                    ok_fmt, fmt_score = validate_ip_number_format(str(val))
                    is_valid = bool(ok_fmt and fmt_score >= 90.0)
            elif rule_type == "ip_number_format_optional":
                # То же, но пустое значение допустимо (номер ИП присваивается не всегда)
                if not val:
                    is_valid = True
                else:
                    ok_fmt, fmt_score = validate_ip_number_format(str(val))
                    is_valid = bool(ok_fmt and fmt_score >= 90.0)
            elif rule_type and rule_type not in known_rules:
                # H-09: неизвестный тип правила не может молча считаться пройденным
                is_valid = False
                issues.append({
                    "field": field_path,
                    "severity": "WARNING",
                    "message": f"Неизвестный тип правила '{rule_type}' в autonomous.json (не реализован в DSL)"
                })
                logger.warning(
                    f"Плагин '{plugin.id}': правило '{rule_type}' для поля '{field_path}' "
                    f"не реализовано в validate_document и помечено как непройденное"
                )
                continue

            if is_valid:
                passed_count += 1
            else:
                issues.append({"field": field_path, "severity": severity, "message": msg})

        score = round((passed_count / total_rules * 100.0), 1) if total_rules > 0 else 100.0
        return {
            "passed": len(issues) == 0,
            "score": score,
            "issues": issues
        }

    # =========================================================================
    # 4. БЕНЧМАРК ПРОТИВ GROUND TRUTH
    # =========================================================================
    def benchmark_against_ground_truth(
        self,
        extracted: Dict[str, Any],
        ground_truth: Dict[str, Any],
        doc_type: str
    ) -> Dict[str, Any]:
        """
        Сравнивает извлеченные данные с эталонными (ground truth) по весам benchmark.json.
        """
        plugin = self.registry.get(doc_type)
        if not plugin:
            return {"accuracy": 0.0, "details": {}}

        bench_fields = plugin.benchmark_config.get("fields", [])
        total_weight = 0.0
        earned_weight = 0.0
        details = {}

        def _get_nested(d: Dict[str, Any], path: str) -> Any:
            parts = path.split(".")
            curr: Any = d
            for p in parts:
                if isinstance(curr, dict):
                    curr = curr.get(p)
                else:
                    return None
            return curr

        for f in bench_fields:
            path = f.get("path", "")
            weight = float(f.get("weight", 1.0))
            m_type = f.get("type", "fuzzy")

            val_ext = _get_nested(extracted, path)
            val_gt = _get_nested(ground_truth, path)

            total_weight += weight
            sim = 0.0

            if val_ext is None and val_gt is None:
                sim = 1.0
            elif val_ext is None or val_gt is None:
                sim = 0.0
            elif m_type == "exact":
                sim = 1.0 if str(val_ext).strip().lower() == str(val_gt).strip().lower() else 0.0
            elif m_type == "numeric":
                try:
                    num_e = float(str(val_ext).replace(" ", "").replace(",", "."))
                    num_g = float(str(val_gt).replace(" ", "").replace(",", "."))
                    sim = 1.0 if abs(num_e - num_g) < 0.01 else max(0.0, 1.0 - abs(num_e - num_g) / max(num_g, 1.0))
                except Exception:
                    sim = 0.0
            else:  # fuzzy
                str_e = str(val_ext).strip().lower()
                str_g = str(val_gt).strip().lower()
                sim = difflib.SequenceMatcher(None, str_e, str_g).ratio()

            earned_weight += sim * weight
            details[path] = {
                "extracted": val_ext,
                "ground_truth": val_gt,
                "similarity": round(sim, 3),
                "weight": weight
            }

        accuracy = round((earned_weight / total_weight * 100.0), 2) if total_weight > 0 else 0.0
        return {
            "accuracy": accuracy,
            "total_weight": total_weight,
            "earned_weight": earned_weight,
            "details": details
        }

    # =========================================================================
    # 5. СКВОЗНАЯ ОБРАБОТКА (ОДИНОЧНАЯ И ПАКЕТНАЯ)
    # =========================================================================
    @staticmethod
    def _combine_quality_and_validation(
        validation: Dict[str, Any],
        quality_score: float,
        quality_status: str,
        zt_report: Any,
    ) -> Tuple[Dict[str, Any], float, str]:
        """
        Связывает Guardrails, Quality Score и Zero-Trust в согласованный итог (S-2):
        - ошибка Zero-Trust -> validation.passed = False, quality_status = needs_attention;
        - низкое разрешение скана (OCR_LOW_CONFIDENCE) -> штраф 10 п.п. к Quality,
          статус не может быть «excellent».
        Раньше рядом жили zero_trust=discrepancy_detected и quality_status=excellent.
        """
        validation = dict(validation)
        issues = list(validation.get("issues", []))
        zt_errors = [i for i in zt_report.issues if i.severity == "error"]

        if zt_errors:
            error_codes = "; ".join(sorted({i.code for i in zt_errors}))
            issues.append({
                "field": "zero_trust",
                "severity": "ERROR",
                "message": f"Zero-Trust обнаружил ошибки: {error_codes}",
            })
            validation["passed"] = False
            quality_status = "needs_attention"

        if zt_report.details.get("scan_low_quality"):
            quality_score = max(0.0, round(float(quality_score) - 10.0, 2))
            issues.append({
                "field": "scan",
                "severity": "WARNING",
                "message": "Низкое разрешение скана (<150 DPI): вероятны ошибки распознавания цифр и ФИО",
            })
            if quality_status == "excellent":
                quality_status = "high"

        # S-13: расхождения кросс-модального гейта (реквизиты не подтверждены
        # текстовым слоем/транскрипцией) — штраф 5 п.п. за каждое, максимум 15
        halluc_count = len(zt_report.details.get("hallucination_discrepancies") or [])
        if halluc_count:
            penalty = min(15.0, 5.0 * halluc_count)
            quality_score = max(0.0, round(float(quality_score) - penalty, 2))
            issues.append({
                "field": "cross_modal",
                "severity": "WARNING",
                "message": f"Кросс-модальный гейт: {halluc_count} реквизит(ов) не подтверждены текстом документа (-{penalty:.0f} п.п.)",
            })
            if quality_status == "excellent":
                quality_status = "high"

        validation["issues"] = issues
        return validation, quality_score, quality_status

    def process_single_document(
        self,
        file_path: str,
        doc_type: Optional[str] = None,
        prompt_user_on_unknown: bool = False
    ) -> Dict[str, Any]:
        """
        Полный конвейер обработки одного документа:
        1. Fast-Path Router (Определение типа)
        2. Specialized Extraction Branch (Извлечение с финансовым сканером)
        3. Guardrails-валидация и расчет Quality Score (%)
        4. Сохранение атомарного JSON в Результаты/ и обновление реестров
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Файл не найден: {file_path}")

        base_name = os.path.basename(file_path)
        logger.info(f"🚀 Запуск обработки документа: {base_name}")

        # 1. Fast-Path Router (Определение типа)
        if not doc_type or doc_type == UNKNOWN_CATEGORY:
            detected_type, conf, method = self.classify_document(file_path)
            doc_type = detected_type
        else:
            conf, method = 1.0, "user_specified"

        logger.info(f"  • Итоговый поток (Branch): '{doc_type}' (достоверность: {conf}, метод: {method})")

        # 2. Specialized Extraction Branch (Извлечение через ветку)
        extracted = self.extract_document_data(file_path, doc_type=doc_type)

        # 3. Валидация Guardrails и расчет Quality Score (%)
        validation = self.validate_document(extracted, doc_type=doc_type)

        # Загрузка ground truth при наличии для бенчмарка
        gt_data = None
        plugin = self.registry.get(doc_type)
        if plugin:
            gt_path = get_ground_truth_path(plugin.gt_file)
            if gt_path and os.path.exists(gt_path):
                try:
                    with open(gt_path, "r", encoding="utf-8") as gf:
                        gt_data = json.load(gf)
                except Exception:
                    gt_data = None

        eval_report = evaluate_dataset([{"data": extracted, "file_name": base_name}], gt_data, doc_type=doc_type)
        doc_eval = eval_report["documents"][0] if eval_report.get("documents") else {}
        quality_score = doc_eval.get("overall_score", validation.get("score", 100.0))
        quality_status = doc_eval.get("status", "excellent")

        # 4. Zero-Trust Верификация (контрольные суммы, математика, хронология, кросс-модальный аудит)
        raw_text = self._extract_raw_text_for_audit(file_path)
        if not raw_text:
            # S-13: для сканов без текстового слоя — второй VLM-проход (точная транскрипция)
            # как эталон для кросс-модальной сверки реквизитов
            ext = os.path.splitext(file_path)[1].lower()
            if ext in SUPPORTED_IMAGE_EXTS or ext == ".pdf":
                raw_text = self._transcribe_scan_for_audit(file_path)
        # M-06: детекция низкого DPI скана для статуса ocr_low_confidence
        scan_dpi = None
        if os.path.splitext(file_path)[1].lower() in SUPPORTED_IMAGE_EXTS:
            try:
                scan_dpi = self.processor.get_image_dpi(file_path)
            except Exception as e:
                logger.debug(f"DPI скана не определен для '{base_name}': {e}")
        zt_report = self.auditor.audit_document(
            data=extracted,
            doc_type=doc_type,
            raw_ocr_text=raw_text,
            extraction_method=method if method == "regex_fallback" else "vlm",
            scan_dpi=scan_dpi,
        )

        # 5. Связность статусов (S-2): Guardrails / Quality / Zero-Trust образуют единый итог
        validation, quality_score, quality_status = self._combine_quality_and_validation(
            validation, quality_score, quality_status, zt_report
        )

        result = {
            "file_name": base_name,
            "file_path": file_path,
            "doc_type": doc_type,
            "doc_type_title": plugin.title if plugin else "Неопределенный документ",
            "classification_confidence": conf,
            "classification_method": method,
            "data": extracted,
            "validation": validation,
            "zero_trust": zt_report.to_dict(),
            "zero_trust_status": zt_report.status.value,
            "quality_score_percent": quality_score,
            "quality_status": quality_status,
            "metrics": doc_eval,
            "status": "COMPLETED" if doc_type != UNKNOWN_CATEGORY else "NEEDS_REVIEW",
            "processed_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        # Атомарное сохранение индивидуального JSON в Результаты/
        out_path = save_single_document_json(result, self.results_dir)
        # Обновление консолидированных JSON реестров
        export_consolidated_registries([result], self.results_dir)

        logger.info(
            f"✅ Обработка завершена: {base_name} -> {out_path} "
            f"(Качество: {quality_score}%, {quality_status} | Zero-Trust: {zt_report.status.value})"
        )
        return result

    def process_batch(
        self,
        folder_or_files: Union[str, List[str]],
        doc_type: Optional[str] = None,
        organize_subfolders: bool = False,
        calculate_metrics: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Последовательный промышленный конвейер пакетной обработки (6 этапов):
        1. Проверка модели и окружения
        2. Поиск и валидация входящих документов
        3. Авто-классификация и роутинг по категориям
        4. Специализированная экстракция с кэшированием и чекпоинтингом
        5. Комплексный расчет метрик качества (Quality Score %)
        6. Экспорт консолидированных JSON-реестров и отчетов
        """
        print("\n" + "=" * 78)
        print(f" 🚀 SCANREADER {_get_version()}: ПОСЛЕДОВАТЕЛЬНЫЙ КОНВЕЙЕР ОБРАБОТКИ ДОКУМЕНТОВ")
        print("=" * 78)

        # ---------------------------------------------------------------------
        # [Этап 1/6] Инициализация окружения и проверка модели
        # ---------------------------------------------------------------------
        print("\n[Этап 1/6] Инициализация окружения и проверка VLM/LLM...")
        client = self._get_client()
        if client:
            print(f"  [OK] Клиент подключен: {self.base_url} (Модель: {self.model_name})")
        else:
            print("  [WARN] Клиент не инициализирован. Проверьте .env конфигурацию.")

        # ---------------------------------------------------------------------
        # [Этап 2/6] Поиск и валидация входящих документов
        # ---------------------------------------------------------------------
        print("\n[Этап 2/6] Поиск и валидация входящих документов...")
        if isinstance(folder_or_files, str) and os.path.isdir(folder_or_files):
            target_files = scan_directory_for_documents(folder_or_files)
            source_info = f"Папка: {folder_or_files}"
        elif isinstance(folder_or_files, list):
            target_files = folder_or_files
            source_info = f"Список файлов ({len(folder_or_files)} шт.)"
        else:
            target_files = [folder_or_files] if os.path.exists(folder_or_files) else []
            source_info = f"Файл: {folder_or_files}"

        if not target_files:
            print(f"  [ВНИМАНИЕ] Поддерживаемых документов не обнаружено в: {source_info}")
            return []

        print(f"  • Источник: {source_info}")
        print(f"  [OK] Найдено документов для обработки: {len(target_files)}")

        # ---------------------------------------------------------------------
        # [Этап 3/6] Авто-классификация и роутинг документов
        # ---------------------------------------------------------------------
        print("\n[Этап 3/6] Автоматическая классификация и роутинг по потокам...")
        grouped_files: Dict[str, List[str]] = {}

        for f_path in target_files:
            if doc_type and doc_type != "auto" and doc_type in self.registry.ids():
                assigned_type = doc_type
            else:
                assigned_type, conf, method = self.classify_document(f_path)
            
            if assigned_type not in grouped_files:
                grouped_files[assigned_type] = []
            grouped_files[assigned_type].append(f_path)

        for cat_k, files_list in grouped_files.items():
            pl = self.registry.get(cat_k)
            t_title = pl.title if pl else "Неопределенный документ"
            print(f"   📂 [{cat_k}] {t_title}: {len(files_list)} файлов")

        # ---------------------------------------------------------------------
        # [Этап 4/6] Специализированная экстракция реквизитов и сумм
        # ---------------------------------------------------------------------
        print("\n[Этап 4/6] Специализированная экстракция данных (Specialized Branches)...")
        results: List[Dict[str, Any]] = []
        errors: List[Dict[str, str]] = []

        for cat_k, cat_files in grouped_files.items():
            pl = self.registry.get(cat_k)
            t_title = pl.title if pl else cat_k
            print(f"\n  👉 Обработка потока '{t_title}' ({len(cat_files)} файлов):")

            checkpoint_file = os.path.join(self.results_dir, f".checkpoint_{cat_k}.json")
            cat_results: List[Dict[str, Any]] = []

            # M-13: возобновление прерванной пакетной обработки по чекпоинту
            resumed = load_checkpoint(checkpoint_file)
            if resumed:
                resumed_names = {r.get("file_name") for r in resumed if isinstance(r, dict)}
                skipped = [f for f in cat_files if os.path.basename(f) in resumed_names]
                if skipped:
                    print(f"    ↩️ Возобновление: {len(skipped)} файлов уже обработаны (чекпоинт), пропускаем.")
                    cat_results.extend(r for r in resumed if isinstance(r, dict) and r.get("status") != "FAILED")
                    results.extend(cat_results)
                cat_files = [f for f in cat_files if os.path.basename(f) not in resumed_names]

            for idx, f_path in enumerate(cat_files, 1):
                f_name = os.path.basename(f_path)
                print(f"    [{idx}/{len(cat_files)}] Обработка: {f_name}...", end="", flush=True)

                t_start = time.perf_counter()
                try:
                    res = self.process_single_document(f_path, doc_type=cat_k)
                    cat_results.append(res)
                    results.append(res)
                    dur = round(time.perf_counter() - t_start, 2)
                    q_score = res.get("quality_score_percent", 0.0)
                    zt_status = res.get("zero_trust_status", "vlm_unverified")
                    print(f" [OK {dur}s | Качество: {q_score}% | ZT: {zt_status}]")
                    save_checkpoint(cat_results, checkpoint_file)
                except Exception as e:
                    dur = round(time.perf_counter() - t_start, 2)
                    print(f" [СБОЙ {dur}s: {e}]")
                    logger.error(f"Сбой при обработке {f_path}: {e}")
                    fail_record = {
                        "file_name": f_name,
                        "file_path": f_path,
                        "doc_type": cat_k,
                        "error": str(e),
                        "status": "FAILED",
                        "processed_at": time.strftime("%Y-%m-%d %H:%M:%S")
                    }
                    results.append(fail_record)
                    errors.append({"file": f_name, "error": str(e)})

            clean_checkpoint(checkpoint_file)

        # ---------------------------------------------------------------------
        # [Этап 5/6] Контроль качества (Quality Score %) и Guardrails
        # ---------------------------------------------------------------------
        print("\n" + "=" * 78)
        print("[Этап 5/6] Расчет метрик качества (Quality Score %) и Guardrails...")
        print(f" • Успешно обработано: {len(results) - len(errors)} | Сбоев: {len(errors)}")

        all_category_metrics: Dict[str, Dict[str, Any]] = {}
        if calculate_metrics and results:
            for cat_k, cat_files in grouped_files.items():
                cat_docs = [r for r in results if r.get("doc_type") == cat_k and r.get("status") != "FAILED"]
                if not cat_docs:
                    continue

                gt_data = None
                pl = self.registry.get(cat_k)
                if pl:
                    gt_path = get_ground_truth_path(pl.gt_file)
                    if gt_path and os.path.exists(gt_path):
                        try:
                            with open(gt_path, "r", encoding="utf-8") as gf:
                                gt_data = json.load(gf)
                        except Exception:
                            gt_data = None

                cat_metrics = evaluate_dataset(cat_docs, gt_data, doc_type=cat_k)
                all_category_metrics[cat_k] = cat_metrics

                # Сохранение метрик категории в JSON
                metric_json_name = f"{cat_k}_quality_metrics.json"
                export_metrics_json(cat_metrics, os.path.join(self.results_dir, metric_json_name))

                mode_label = "🎯 Ground Truth Benchmark" if gt_data else "🛡️ Autonomous Guardrails"
                print(f"  📊 [{cat_k}] Среднее качество: {cat_metrics['average_quality_score_percent']}% ({mode_label})")
                st = cat_metrics.get("status_counts", {})
                print(f"       🟢 Отлично: {st.get('excellent', 0)} | 🟡 Высокое: {st.get('high', 0)} | 🟠 Удовл.: {st.get('satisfactory', 0)} | 🔴 Внимание: {st.get('needs_attention', 0)}")

            # Сводный отчет запуска (Run Summary)
            if all_category_metrics:
                run_summary = generate_run_summary(all_category_metrics)
                export_metrics_json(run_summary, os.path.join(self.results_dir, "run_metrics_summary.json"))
                export_run_summary_markdown(run_summary, os.path.join(self.results_dir, "run_metrics_summary.md"))
                export_run_summary_excel(run_summary, os.path.join(self.results_dir, "run_metrics_summary.xlsx"))
                append_to_metrics_history(run_summary, os.path.join(self.results_dir, "metrics_history.json"))
                print(f"\n  🏆 СВОДНЫЙ QUALITY SCORE ЗАПУСКА: {run_summary['overall_quality_score_percent']}%")

        # ---------------------------------------------------------------------
        # [Этап 6/6] Формирование структурированных JSON-реестров и Excel
        # ---------------------------------------------------------------------
        print("\n[Этап 6/6] Формирование консолидированных JSON-реестров...")
        saved_registries = export_consolidated_registries(results, self.results_dir)
        for r_name in saved_registries:
            print(f"  💾 Сохранен реестр: Результаты/{r_name}")

        # Экспорт в сводный Excel
        try:
            from .excel_exporter import LegalExcelExporter
            exporter = LegalExcelExporter(output_dir=self.results_dir)
            valid_results = [r for r in results if r.get("status") != "FAILED"]
            if valid_results:
                excel_path = exporter.export_results_to_excel(valid_results)
                print(f"  📊 Сформирован Excel отчет: {os.path.basename(excel_path)}")
        except Exception as ex:
            logger.warning(f"Excel экспорт пропущен: {ex}")

        # Физическая раскладка по подпапкам при флаге organize_subfolders
        if organize_subfolders:
            import shutil
            print("\n  📂 Физическая раскладка файлов по тематическим папкам...")
            inc_env = os.getenv("SCANREADER_INCOMING_DIR")
            if inc_env and os.path.exists(inc_env):
                inc_root = inc_env
            elif os.path.exists(os.path.join(os.getcwd(), "incoming")):
                inc_root = os.path.join(os.getcwd(), "incoming")
            elif os.path.exists(os.path.join(os.getcwd(), "Входящие_документы")):
                inc_root = os.path.join(os.getcwd(), "Входящие_документы")
            elif os.path.exists(os.path.join(ROOT_DIR, "incoming")):
                inc_root = os.path.join(ROOT_DIR, "incoming")
            else:
                inc_root = os.path.join(ROOT_DIR, "Входящие_документы")
            for item in results:
                src_path = item.get("file_path")
                dt = item.get("doc_type") if isinstance(item.get("doc_type"), str) else None
                pl = self.registry.get(dt) if dt else None
                if pl and src_path and os.path.exists(src_path):
                    target_sub = pl.id if os.path.exists(os.path.join(inc_root, pl.id)) or not os.path.exists(os.path.join(inc_root, pl.folder)) else pl.folder
                    target_folder = os.path.join(inc_root, target_sub)
                    os.makedirs(target_folder, exist_ok=True)
                    target_file = os.path.join(target_folder, os.path.basename(src_path))
                    if os.path.abspath(src_path) != os.path.abspath(target_file):
                        try:
                            # H-17: Безопасное копирование вместо разрушительного перемещения
                            shutil.copy2(src_path, target_file)
                            item["file_path"] = target_file
                            print(f"    -> Скопирован: {os.path.basename(src_path)} -> {target_sub}/")
                        except Exception as e:
                            logger.warning(f"Не удалось скопировать {src_path}: {e}")

        print("\n" + "=" * 78)
        print(" ✅ ВСЕ ЭТАПЫ ОБРАБОТКИ УСПЕШНО ЗАВЕРШЕНЫ!")
        print(f" • Каталог результатов: {self.results_dir}")
        print("=" * 78 + "\n")

        return results

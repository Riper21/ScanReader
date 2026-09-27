"""
Модуль детерминированного кэширования ответов VLM / LLM (core/cache.py).
Обеспечивает мгновенные повторные прогоны по неизменным изображениям/документам,
сохраняя результаты на диск с контролем размера кэша и атомарной записью (H-10, H-11, H-03).
"""

import os
import json
import hashlib
import threading
from pathlib import Path
from typing import Optional, Dict
from .utils import get_logger
from .io_utils import write_atomic

logger = get_logger("core.cache")


class LLMResponseCache:
    """
    Детерминированный файловый кэш для VLM / LLM ответов.
    Хеширует входные данные (промпт + хеши изображений/текста) через SHA-256
    и мгновенно возвращает кэшированный результат при повторном анализе.
    """

    def __init__(
        self,
        cache_dir: Optional[Path] = None,
        enabled: Optional[bool] = None,
        max_entries: Optional[int] = None
    ):
        if enabled is not None:
            self.enabled = enabled
        else:
            self.enabled = os.environ.get("ENABLE_CACHE", "true").lower() in ("true", "1", "yes")

        # H-10: Размещение кэша вне дерева исходников пакета
        if cache_dir:
            base_dir = Path(cache_dir)
        else:
            env_cache_dir = os.getenv("SCANREADER_CACHE_DIR")
            if env_cache_dir:
                base_dir = Path(env_cache_dir)
            else:
                base_dir = Path.cwd() / ".cache" / "scan_reader"

        self.max_entries = max_entries or int(os.getenv("SCANREADER_MAX_CACHE_ENTRIES", "2000"))
        self.cache_dir = base_dir
        self.cache_file = base_dir / "vlm_cache.json"
        self._memory_cache: Dict[str, str] = {}
        self._lock = threading.Lock()
        self._load_cache()

    def __len__(self) -> int:
        """Количество записей в кэше (для инвариантов размера)."""
        with self._lock:
            return len(self._memory_cache)

    def _load_cache(self) -> None:
        """Загрузка сохраненного кэша с диска."""
        if not self.enabled:
            return
        if self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    self._memory_cache = json.load(f)
            except Exception as e:
                logger.warning(f"Не удалось прочитать файл кэша {self.cache_file}: {e}")
                self._memory_cache = {}

    def _save_cache(self) -> None:
        """Сохранение кэша на диск в атомарном режиме (H-03, H-10)."""
        if not self.enabled:
            return
        try:
            write_atomic(self.cache_file, json.dumps(self._memory_cache, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"Не удалось записать кэш в {self.cache_file}: {e}")

    @staticmethod
    def compute_key(system_prompt: str, user_content: str, model_name: str = "") -> str:
        """Вычисление детерминированного SHA-256 ключа запроса (H-11)."""
        combined = f"{model_name}::{system_prompt}::{user_content}"
        return hashlib.sha256(combined.encode("utf-8", errors="ignore")).hexdigest()

    def get(self, key: str) -> Optional[str]:
        """Получить ответ из кэша по ключу."""
        if not self.enabled:
            return None
        with self._lock:
            return self._memory_cache.get(key)

    def set(self, key: str, value: str) -> None:
        """Записать ответ в кэш с лимитом размера (H-10)."""
        if not self.enabled:
            return
        with self._lock:
            if len(self._memory_cache) >= self.max_entries and key not in self._memory_cache:
                oldest_key = next(iter(self._memory_cache))
                del self._memory_cache[oldest_key]
            self._memory_cache[key] = value
            self._save_cache()

    def clear(self) -> None:
        """Очистить кэш в памяти и на диске."""
        with self._lock:
            self._memory_cache.clear()
            if self.cache_file.exists():
                try:
                    self.cache_file.unlink()
                except Exception as e:
                    logger.warning(f"Не удалось удалить файл кэша {self.cache_file}: {e}")

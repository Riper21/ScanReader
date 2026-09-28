"""
Модуль детерминированного кэширования ответов VLM / LLM (core/cache.py).
Обеспечивает мгновенные повторные прогоны по неизменным изображениям/документам,
сохраняя результаты на диск с контролем размера кэша и атомарной записью (H-10, H-11, H-03).
"""

import os
import json
import time
import hashlib
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional
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
        # Фаза 7.6: отложенный сброс на диск вместо записи на каждую вставку
        self.flush_every = int(os.getenv("SCANREADER_CACHE_FLUSH_EVERY", "25") or 25)
        self.flush_interval = float(os.getenv("SCANREADER_CACHE_FLUSH_SECONDS", "30") or 30)
        self._memory_cache: "OrderedDict[str, str]" = OrderedDict()
        self._lock = threading.Lock()
        self._dirty = 0
        self._last_flush = time.monotonic()
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
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    self._memory_cache = OrderedDict(loaded)
                else:
                    logger.warning(f"Файл кэша {self.cache_file} имеет неверный формат, игнорируем")
                    self._memory_cache = OrderedDict()
            except Exception as e:
                logger.warning(f"Не удалось прочитать файл кэша {self.cache_file}: {e}")
                self._memory_cache = OrderedDict()

    @staticmethod
    def compute_key(system_prompt: str, user_content: str, model_name: str = "") -> str:
        """Вычисление детерминированного SHA-256 ключа запроса (H-11)."""
        combined = f"{model_name}::{system_prompt}::{user_content}"
        return hashlib.sha256(combined.encode("utf-8", errors="ignore")).hexdigest()

    def get(self, key: str) -> Optional[str]:
        """Получить ответ из кэша по ключу (попадание повышает LRU-приоритет)."""
        if not self.enabled:
            return None
        with self._lock:
            value = self._memory_cache.get(key)
            if value is not None:
                self._memory_cache.move_to_end(key)
            return value

    def set(self, key: str, value: str) -> None:
        """
        Записать ответ в кэш с лимитом размера (H-10).

        Фаза 7.6: дисковое сохранение вынесено ИЗ-ПОД ЗАМКА и стало
        отложенным. Раньше каждый вызов сериализовал весь кэш в JSON и писал его
        с fsync, удерживая блокировку, то есть на каждый документ выполнялась
        работа O(n) — суммарно O(n²) за прогон. Теперь запись батчится и
        досписывается по таймеру либо при заполнении, а close()/flush() дают
        гарантию перед выходом процесса.
        """
        if not self.enabled:
            return
        with self._lock:
            if len(self._memory_cache) >= self.max_entries and key not in self._memory_cache:
                # LRU вместо FIFO: next(iter(...)) вытеснял самый старый ПО
                # ПОРЯДКУ ВСТАВКИ, то есть повторно используемые записи вытеснялись
                # наравне с бесполезными.
                self._memory_cache.move_to_end(key) if key in self._memory_cache else None
                self._memory_cache[key] = value
                oldest_key = next(iter(self._memory_cache))
                if oldest_key != key:
                    del self._memory_cache[oldest_key]
            else:
                self._memory_cache[key] = value
            self._dirty += 1
            should_flush = self._dirty >= self.flush_every or (time.monotonic() - self._last_flush) >= self.flush_interval
        if should_flush:
            self.flush()

    def flush(self) -> None:
        """Принудительно сбросить кэш на диск. Вызывается перед завершением прогона."""
        with self._lock:
            if not self._dirty:
                return
            snapshot = dict(self._memory_cache)
            self._dirty = 0
            self._last_flush = time.monotonic()
        try:
            write_atomic(self.cache_file, json.dumps(snapshot, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.warning(f"Не удалось записать кэш в {self.cache_file}: {e}")

    def close(self) -> None:
        """Сбросить кэш на диск при завершении работы."""
        self.flush()

    def __enter__(self) -> "LLMResponseCache":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def __del__(self) -> None:  # pragma: no cover - зависит от порядка сборки мусора
        try:
            self.flush()
        except Exception:
            pass

    def clear(self) -> None:
        """Очистить кэш в памяти и на диске."""
        with self._lock:
            self._memory_cache.clear()
            self._dirty = 0
            if self.cache_file.exists():
                try:
                    self.cache_file.unlink()
                except Exception as e:
                    logger.warning(f"Не удалось удалить файл кэша {self.cache_file}: {e}")

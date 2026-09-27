"""
Модуль управления частотой запросов и параллелизмом VLM/LLM (core/rate_limiter.py).
Реализует потокобезопасный алгоритм Token Bucket и семафор максимального параллелизма (Concurrency Guard)
для предотвращения перегрузки локальных GPU при пакетной обработке и защиты от ошибок 429.
"""

import os
import time
import threading
from typing import Optional
from .utils import get_logger

logger = get_logger("core.rate_limiter")


class RateLimitTimeoutError(RuntimeError):
    """Исключение, возникающее при превышении таймаута ожидания слота или токена в RateLimiter."""
    pass

# Алиас для совместимости
RateLimitTimeout = RateLimitTimeoutError


class RateLimiter:
    """
    Потокобезопасный ограничитель частоты запросов (RPM) и максимального параллелизма (Concurrency).
    """
    _instance: Optional["RateLimiter"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        rpm_limit: Optional[int] = None,
        max_concurrency: Optional[int] = None,
        enabled: Optional[bool] = None,
        timeout: Optional[float] = None
    ):
        self.rpm_limit = rpm_limit if rpm_limit is not None else int(os.getenv("VLM_RPM_LIMIT", "60"))
        self.max_concurrency = max_concurrency if max_concurrency is not None else int(os.getenv("VLM_MAX_CONCURRENCY", "2"))
        self.timeout = timeout if timeout is not None else float(os.getenv("VLM_TIMEOUT", "120.0"))
        if enabled is not None:
            self.enabled = enabled
        else:
            self.enabled = os.getenv("ENABLE_RATE_LIMITER", "true").lower() in ("true", "1", "yes")

        # Token Bucket параметры с поддержкой Burst-старта
        self.capacity = max(float(self.rpm_limit), float(self.max_concurrency))
        self.tokens = self.capacity
        self.fill_rate = float(self.rpm_limit) / 60.0  # токенов в секунду
        self.last_update = time.monotonic()  # H-14: использование monotonic вместо time()
        self._bucket_lock = threading.Lock()

        # Семафор параллелизма с жестким верхним пределом (C-11)
        self._semaphore = threading.BoundedSemaphore(self.max_concurrency)
        self._local = threading.local()

    @classmethod
    def get_limiter(cls) -> "RateLimiter":
        """Получить синглтон-экземпляр RateLimiter."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def acquire(self, timeout: float = 120.0) -> bool:
        """
        Захват квоты на выполнение одного запроса.
        Ожидает освобождения слота в семафоре и наличия токена в бакете.
        """
        if not self.enabled:
            return True

        # 1. Захват слота параллелизма
        acquired_sem = self._semaphore.acquire(timeout=timeout)
        if not acquired_sem:
            logger.warning("⚠️ [RateLimiter] Превышен таймаут ожидания слота параллелизма GPU/VLM.")
            return False

        # 2. Пополнение и списание токена Token Bucket с time.monotonic (H-14)
        start_wait = time.monotonic()
        while True:
            with self._bucket_lock:
                now = time.monotonic()
                elapsed = now - self.last_update
                self.last_update = now
                self.tokens = min(self.capacity, self.tokens + elapsed * self.fill_rate)

                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return True

            if time.monotonic() - start_wait > timeout:
                self._semaphore.release()
                logger.warning("⚠️ [RateLimiter] Превышен таймаут ожидания токена в Token Bucket.")
                return False

            time.sleep(0.05)

    def release(self) -> None:
        """Освобождение слота параллелизма после завершения запроса."""
        if not self.enabled:
            return
        try:
            self._semaphore.release()
        except ValueError as e:
            logger.warning(f"⚠️ [RateLimiter] Попытка повторного освобождения семафора: {e}")

    def __enter__(self):
        """
        Вход в контекстный менеджер (C-11).
        Если захват не удался, выбрасывает RateLimitTimeoutError, предотвращая неконтролируемый запрос.
        """
        acquired = self.acquire(timeout=self.timeout)
        if not hasattr(self._local, "acquired_depth"):
            self._local.acquired_depth = 0
        if acquired:
            self._local.acquired_depth += 1
            return self
        else:
            raise RateLimitTimeoutError("Превышен таймаут ожидания слота/токена RateLimiter")

    def __exit__(self, exc_type, exc_val, exc_tb):
        """
        Выход из контекстного менеджера (C-11).
        Освобождает слот только если он был действительно захвачен.
        """
        if getattr(self._local, "acquired_depth", 0) > 0:
            self._local.acquired_depth -= 1
            self.release()

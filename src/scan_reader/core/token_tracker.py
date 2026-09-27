"""
Модуль отслеживания расхода токенов, времени обработки и скорости VLM (Token & Latency Tracker).
Предоставляет потокобезопасный синглтон TokenUsageTracker для регистрации метрик вызовов моделей.
"""

import threading
from collections import deque
from typing import Optional, Dict, Any
from .utils import get_logger

logger = get_logger("core.token_tracker")


class LLMCallRecord:
    """Запись об отдельном вызове VLM/LLM."""

    def __init__(
        self,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_sec: float = 0.0,
        is_cache_hit: bool = False,
        stage_name: str = "extraction",
        doc_name: str = ""
    ):
        self.model = model
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens or (prompt_tokens + completion_tokens)
        self.latency_sec = latency_sec
        self.is_cache_hit = is_cache_hit
        self.stage_name = stage_name
        self.doc_name = doc_name
        self.tokens_per_sec = (
            round(completion_tokens / latency_sec, 2)
            if latency_sec > 0 and completion_tokens > 0
            else 0.0
        )


class TokenUsageTracker:
    """Потокобезопасный синглтон-трекер потребления токенов и задержек VLM/LLM (H-12, H-13)."""
    _instance: Optional["TokenUsageTracker"] = None
    _lock = threading.Lock()

    def __init__(self, max_records: int = 5000):
        # H-12: Ограничение размера очереди записей для защиты от утечек памяти
        self.records: deque = deque(maxlen=max_records)
        self._current_stage: str = "general"
        # Кумулятивные счетчики (не теряются при вытеснении записей из deque)
        self._total_calls: int = 0
        self._total_prompt_tokens: int = 0
        self._total_completion_tokens: int = 0

    @classmethod
    def get_tracker(cls) -> "TokenUsageTracker":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def set_stage(self, stage_name: str) -> None:
        """Устанавливает текущий логический этап конвейера под блокировкой (H-13)."""
        with self._lock:
            self._current_stage = stage_name

    def record_call(
        self,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        latency_sec: float = 0.0,
        is_cache_hit: bool = False,
        stage_name: Optional[str] = None,
        doc_name: str = ""
    ) -> None:
        """Регистрирует метрики выполнения запроса."""
        with self._lock:
            stage = stage_name or self._current_stage
            if total_tokens == 0 and (prompt_tokens > 0 or completion_tokens > 0):
                total_tokens = prompt_tokens + completion_tokens

            record = LLMCallRecord(
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_sec=latency_sec,
                is_cache_hit=is_cache_hit,
                stage_name=stage,
                doc_name=doc_name
            )
            self._total_calls += 1
            self._total_prompt_tokens += prompt_tokens
            self._total_completion_tokens += completion_tokens
            self.records.append(record)

    def get_summary(self) -> Dict[str, Any]:
        """Возвращает агрегированную сводку потребления токенов."""
        with self._lock:
            total_calls = len(self.records)
            prompt_tokens = sum(r.prompt_tokens for r in self.records)
            completion_tokens = sum(r.completion_tokens for r in self.records)
            total_tokens = sum(r.total_tokens for r in self.records)
            total_latency = sum(r.latency_sec for r in self.records)
            cache_hits = sum(1 for r in self.records if r.is_cache_hit)
            real_calls = total_calls - cache_hits

            avg_speed = (
                round(completion_tokens / total_latency, 2)
                if total_latency > 0 and completion_tokens > 0
                else 0.0
            )
            avg_latency = (
                round(total_latency / real_calls, 3)
                if real_calls > 0
                else 0.0
            )

            # Разбивка по этапам
            by_stage: Dict[str, Dict[str, Any]] = {}
            for r in self.records:
                st = r.stage_name
                if st not in by_stage:
                    by_stage[st] = {"calls": 0, "total_tokens": 0, "latency_sec": 0.0}
                by_stage[st]["calls"] += 1
                by_stage[st]["total_tokens"] += r.total_tokens
                by_stage[st]["latency_sec"] += r.latency_sec

            return {
                "total_calls": total_calls,
                "real_calls": real_calls,
                "cache_hits": cache_hits,
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "total_latency_sec": round(total_latency, 2),
                "avg_latency_sec": avg_latency,
                "avg_speed_tokens_per_sec": avg_speed,
                "by_stage": by_stage
            }

    def print_summary(self) -> None:
        """Красивый консольный вывод сводки по токенам и времени."""
        summary = self.get_summary()
        print("\n" + "=" * 65)
        print("          📊 СТАТИСТИКА ВЫЗОВОВ VLM / LLM И ТОКЕНОВ")
        print("=" * 65)
        print(f"  • Всего вызовов (Calls):        {summary['total_calls']} (из кэша: {summary['cache_hits']})")
        print(f"  • Входных токенов (Prompt):     {summary['prompt_tokens']:,}")
        print(f"  • Выходных токенов (Completion): {summary['completion_tokens']:,}")
        print(f"  • Суммарно токенов (Total):     {summary['total_tokens']:,}")
        print(f"  • Общее время работы (Latency):  {summary['total_latency_sec']} сек.")
        if summary['real_calls'] > 0:
            print(f"  • Средняя скорость генерации:   {summary['avg_speed_tokens_per_sec']} токенов/сек.")
            print(f"  • Среднее время на документ:    {summary['avg_latency_sec']} сек.")
        print("=" * 65 + "\n")

    def reset(self) -> None:
        """Сброс всех записей трекера."""
        with self._lock:
            self.records.clear()
            self._total_calls = 0
            self._total_prompt_tokens = 0
            self._total_completion_tokens = 0

    @property
    def total_calls(self) -> int:
        """Кумулятивное количество вызовов (не уменьшается при вытеснении записей из deque)."""
        with self._lock:
            return self._total_calls

    @property
    def total_prompt_tokens(self) -> int:
        """Кумулятивное количество входных токенов."""
        with self._lock:
            return self._total_prompt_tokens

    @property
    def total_completion_tokens(self) -> int:
        """Кумулятивное количество выходных токенов."""
        with self._lock:
            return self._total_completion_tokens

"""
Утилита комплексной диагностики VLM / LLM шлюза, сети и скорости генерации (core/diagnostics.py).
Позволяет протестировать:
1. Конфигурацию переменных окружения (.env).
2. Сетевую доступность и задержку (Ping / Latency) до шлюза (Ollama / vLLM / OpenAI).
3. Аутентификацию и отклик модели.
4. Скорость генерации токенов (tokens/sec) и задержку первого ответа (TTFT).
"""

import os
import sys
import time
from pathlib import Path
from typing import Dict, Any
from dotenv import load_dotenv

from .utils import setup_console_utf8, get_logger
from .token_tracker import TokenUsageTracker

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent

# Загрузка .env из текущего каталога и корня проекта (C-13)
load_dotenv(Path.cwd() / ".env")
load_dotenv(ROOT_DIR.parent / ".env")

logger = get_logger("core.diagnostics")


def mask_key(key: str) -> str:
    """Маскирование API-ключа для безопасного вывода."""
    if not key:
        return "Не задан (None)"
    if key in ("dummy_local_key", "local", "none"):
        return f"{key} (локальный режим)"
    if len(key) <= 8:
        return "***"
    return f"{key[:4]}...{key[-4:]}"


def check_network_ping(base_url: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Замер сетевой задержки (Ping) до хоста шлюза."""
    import urllib.parse
    parsed = urllib.parse.urlparse(base_url)
    host = parsed.netloc or parsed.path
    if not host:
        return {"status": "error", "error": "Некорректный URL", "latency_ms": 0}

    import urllib.request
    start_time = time.perf_counter()
    try:
        req = urllib.request.Request(
            base_url.rstrip("/"),
            headers={"User-Agent": "LegalDocPlatform-Diagnostics/1.0"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return {
                "status": "ok",
                "status_code": resp.getcode(),
                "latency_ms": latency_ms,
                "url": base_url
            }
    except Exception as e:
        latency_ms = round((time.perf_counter() - start_time) * 1000, 2)
        status_code = getattr(e, 'code', None)
        if status_code in (200, 404, 405):
            return {
                "status": "ok",
                "status_code": status_code,
                "latency_ms": latency_ms,
                "url": base_url
            }
        return {
            "status": "warning",
            "error": str(e),
            "latency_ms": latency_ms,
            "url": base_url
        }


def get_system_report() -> str:
    """Формирует читаемый текстовый отчет о состоянии окружения и каталогов системы."""
    report = run_diagnostics(verbose=False)
    lines = [
        "ScanReader System Report:",
        f"  Status:        {report.get('status', 'unknown')}",
        f"  Python:        {report.get('environment', {}).get('python_version', 'unknown')}",
        f"  Platform:      {report.get('environment', {}).get('platform', 'unknown')}",
        f"  Working dir:   {report.get('environment', {}).get('cwd', 'unknown')}",
        f"  Output dir:    {report.get('directories', {}).get('output_dir', 'unknown')}",
        f"  Ground truth:  {report.get('directories', {}).get('ground_truth_dir', 'unknown')}",
        f"  Cache dir:     {report.get('directories', {}).get('cache_dir', 'unknown')}",
    ]
    deps = report.get("dependencies", {})
    missing = [name for name, ok in deps.items() if not ok]
    if missing:
        lines.append(f"  Missing deps:  {', '.join(missing)}")
    else:
        lines.append("  Dependencies:   all core modules available")
    if report.get("vlm", {}).get("status") == "ok":
        lines.append(f"  VLM endpoint:   reachable ({report['vlm'].get('base_url', '')})")
    else:
        lines.append(f"  VLM endpoint:   unreachable ({report.get('vlm', {}).get('base_url', 'unknown')})")
    return "\n".join(lines)


def run_diagnostics(verbose: bool = False, check_vlm: bool = True) -> Dict[str, Any]:
    """
    Комплексная системная диагностика окружения ScanReader (без обращения к модели).
    Проверяет переменные окружения, рабочие каталоги и доступность зависимостей.
    Возвращает агрегированный статус 'ok' / 'warning' / 'error'.
    """
    setup_console_utf8()

    env_info = {
        "python_version": sys.version.split()[0],
        "platform": sys.platform,
        "cwd": os.getcwd(),
        "openai_base_url": os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1"),
        "model_name": os.getenv("AI_MODEL_NAME", "qwen2.5-vl:7b"),
        "api_key": mask_key(os.getenv("OPENAI_API_KEY", "dummy_local_key")),
        "log_level": os.getenv("LOG_LEVEL", "INFO"),
        "cache_enabled": os.getenv("ENABLE_CACHE", "true"),
    }

    output_dir = os.getenv("SCANREADER_OUTPUT_DIR") or os.path.join(os.getcwd(), "output")
    cache_dir = os.getenv("SCANREADER_CACHE_DIR") or os.path.join(os.getcwd(), ".cache", "scan_reader")

    gt_dir = os.getenv("SCANREADER_GROUND_TRUTH_DIR") or os.path.join(os.getcwd(), "data", "ground_truth")

    directories = {
        "output_dir": output_dir,
        "output_dir_exists": os.path.isdir(output_dir),
        "cache_dir": cache_dir,
        "cache_dir_exists": os.path.isdir(cache_dir),
        "ground_truth_dir": gt_dir,
        "ground_truth_dir_exists": os.path.isdir(gt_dir),
    }

    core_deps = ("pydantic", "openai", "PIL", "fitz", "openpyxl", "dotenv")
    optional_deps = ("pandas", "docx")
    dependencies: Dict[str, bool] = {}
    for mod in core_deps + optional_deps:
        try:
            __import__(mod)
            dependencies[mod] = True
        except Exception:
            dependencies[mod] = False

    missing_core = [m for m in core_deps if not dependencies.get(m)]
    missing_optional = [m for m in optional_deps if not dependencies.get(m)]

    vlm_check: Dict[str, Any] = {"status": "skipped"}
    if check_vlm:
        base_url = str(env_info["openai_base_url"])
        vlm_check = {"status": "warning", "base_url": base_url}
        try:
            ping = check_network_ping(base_url, timeout=3.0)
            vlm_check["ping"] = ping
            vlm_check["status"] = "ok" if ping.get("status") == "ok" else "warning"
        except Exception as e:
            vlm_check["status"] = "warning"
            vlm_check["error"] = str(e)

    if missing_core:
        status = "error"
    elif missing_optional or vlm_check.get("status") != "ok":
        status = "warning"
    else:
        status = "ok"

    report: Dict[str, Any] = {
        "status": status,
        "environment": env_info,
        "directories": directories,
        "dependencies": dependencies,
        "vlm": vlm_check,
    }
    if status == "error":
        report["error"] = f"Missing core dependencies: {', '.join(missing_core)}"

    if verbose:
        print("\n" + "=" * 70)
        print("      🩺 СИСТЕМНАЯ ДИАГНОСТИКА ОКРУЖЕНИЯ SCANREADER")
        print("=" * 70)
        print(f"  Python:        {env_info['python_version']} ({env_info['platform']})")
        print(f"  Каталог:       {env_info['cwd']}")
        print(f"  Output:        {output_dir} ({'OK' if directories['output_dir_exists'] else 'не найден'})")
        print(f"  Ground truth:  {gt_dir} ({'OK' if directories['ground_truth_dir_exists'] else 'не найден'})")
        print(f"  VLM шлюз:      {env_info['openai_base_url']} -> {vlm_check.get('status')}")
        print(f"  Статус:        {status}")
        print("=" * 70 + "\n")

    return report


def run_vlm_diagnostics(verbose: bool = True) -> Dict[str, Any]:
    """Полный запуск диагностического теста VLM / LLM шлюза."""
    setup_console_utf8()
    tracker = TokenUsageTracker.get_tracker()
    tracker.reset()

    if verbose:
        print("\n" + "=" * 70)
        print("      🩺 ДИАГНОСТИКА ПОДКЛЮЧЕНИЯ И ПРОИЗВОДИТЕЛЬНОСТИ VLM / LLM")
        print("=" * 70)

    # 1. Проверка конфигурации
    api_key = os.getenv("OPENAI_API_KEY", "dummy_local_key")
    base_url = os.getenv("OPENAI_BASE_URL", "http://localhost:11434/v1")
    model_name = os.getenv("AI_MODEL_NAME", "qwen2.5-vl:7b")

    if verbose:
        print("\n📋 1. Параметры конфигурации:")
        print(f"  • Модель (AI_MODEL_NAME):       {model_name}")
        print(f"  • Шлюз (OPENAI_BASE_URL):       {base_url}")
        print(f"  • API-ключ:                     {mask_key(api_key)}")

    # 2. Сетевой тест
    if verbose:
        print("\n🌐 2. Проверка сетевого соединения (Ping)...")
    ping_res = check_network_ping(base_url)
    if verbose:
        if ping_res.get("status") == "ok":
            print(f"  ✅ Хост доступен: {base_url} (Задержка: {ping_res.get('latency_ms')} мс)")
        else:
            print(f"  ⚠️ Внимание: {ping_res.get('error')} (Задержка: {ping_res.get('latency_ms')} мс)")

    # 3. Тест генерации (Inference Benchmark)
    if verbose:
        print("\n⚡ 3. Тестовый запрос к модели (Замер TTFT и скорости)...")

    results: Dict[str, Any] = {
        "config": {"model": model_name, "base_url": base_url, "api_key": mask_key(api_key)},
        "ping": ping_res,
        "inference": {"status": "skipped"}
    }

    try:
        from openai import OpenAI
        client = OpenAI(
            base_url=base_url,
            api_key=api_key or "dummy_local_key",
            timeout=15.0
        )

        test_prompt = "Ответь строго одним словом в формате JSON: {\"status\": \"READY\"}"
        start_t = time.perf_counter()
        resp = client.chat.completions.create(
            model=model_name,
            messages=[{"role": "user", "content": test_prompt}],
            temperature=0.0,
            max_tokens=50
        )
        total_time = time.perf_counter() - start_t
        reply = (resp.choices[0].message.content or "").strip()
        usage = resp.usage

        prompt_tokens = usage.prompt_tokens if usage else 0
        comp_tokens = usage.completion_tokens if usage else 0
        total_tokens = usage.total_tokens if usage else (prompt_tokens + comp_tokens)

        speed = round(comp_tokens / total_time, 2) if total_time > 0 and comp_tokens > 0 else 0.0

        results["inference"] = {
            "status": "ok",
            "reply": reply,
            "latency_sec": round(total_time, 3),
            "prompt_tokens": prompt_tokens,
            "completion_tokens": comp_tokens,
            "tokens_per_sec": speed
        }

        tracker.record_call(
            model=model_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=comp_tokens,
            total_tokens=total_tokens,
            latency_sec=total_time,
            stage_name="diagnostics"
        )

        if verbose:
            print(f"  ✅ Ответ получен за {round(total_time, 3)} сек.")
            print(f"  • Содержимое ответа: {reply}")
            print(f"  • Использовано токенов: {total_tokens} (вход: {prompt_tokens}, выход: {comp_tokens})")
            if speed > 0:
                print(f"  • Скорость генерации: {speed} токенов/сек.")

    except Exception as e:
        results["inference"] = {"status": "error", "error": str(e)}
        if verbose:
            print(f"  ⚠️ Тестовый запрос не выполнен: {e}")
            print("  💡 Проверьте, запущен ли сервис Ollama/vLLM или доступен ли OPENAI_BASE_URL.")

    # Агрегация общего статуса диагностики (C-07)
    inf_status = results.get("inference", {}).get("status")

    if inf_status == "ok":
        results["status"] = "ok"
    else:
        results["status"] = "error"
        err_msg = results.get("inference", {}).get("error") or results.get("ping", {}).get("error") or "VLM/LLM unreachable"
        results["error"] = err_msg

    if verbose:
        print("\n" + "=" * 70)
        print("                   🏁 ДИАГНОСТИКА ЗАВЕРШЕНА")
        print("=" * 70 + "\n")

    return results


if __name__ == "__main__":
    run_vlm_diagnostics(verbose=True)

# -*- coding: utf-8 -*-
"""
Конфигурация тестовой среды Pytest.
H-18: изоляция окружения — тесты не читают реальный .env, не пишут в реальный кэш,
не ходят во внешнюю сеть и сбрасывают синглтоны между прогонами.
"""

import socket
import sys
from pathlib import Path
import pytest

TESTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TESTS_DIR.parent
SRC_DIR = PROJECT_ROOT / "src"

# Put src at the absolute front of sys.path (единственный источник пакета scan_reader)
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))


LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


@pytest.fixture(autouse=True)
def _isolate_runtime_env(monkeypatch, tmp_path):
    """H-18: тесты не зависят от реального .env и не пишут в реальный кэш/выход."""
    monkeypatch.setenv("OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy_local_key")
    monkeypatch.setenv("AI_MODEL_NAME", "test-model")
    monkeypatch.setenv("ENABLE_CACHE", "false")
    monkeypatch.setenv("SCANREADER_CACHE_DIR", str(tmp_path / "sr_test_cache"))
    monkeypatch.setenv("SCANREADER_OUTPUT_DIR", str(tmp_path / "output"))
    monkeypatch.setenv("SCANREADER_GROUND_TRUTH_DIR", str(tmp_path / "gt"))


@pytest.fixture(autouse=True)
def _block_external_network(monkeypatch):
    """H-18: блокировка обращений к внешним хостам (localhost разрешен)."""
    real_connect = socket.socket.connect

    def guarded_connect(self, address):
        host = address[0] if isinstance(address, tuple) else str(address)
        if isinstance(host, str) and host not in LOCAL_HOSTS:
            raise OSError(f"Network access to external host blocked in tests: {host}")
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """H-18: сброс синглтонов трекера и лимитера после каждого теста."""
    yield
    from scan_reader.core.token_tracker import TokenUsageTracker
    from scan_reader.core.rate_limiter import RateLimiter
    TokenUsageTracker._instance = None
    RateLimiter._instance = None


@pytest.fixture
def sample_enforcement_order_data():
    return {
        "file_name": "test_order.jpg",
        "doc_type": "Заявление взыскателя о возбуждении ИП",
        "doc_date": "17.06.2015",
        "reg_number": "Вх. № 514",
        "ip_number": "",
        "fssp": {
            "name": "Химкинский РОСП",
            "region": "Московская область",
            "address": "г. Химки, ул. Победы, 3",
            "officer": "Смирнов В.П."
        },
        "court": {
            "name": "Химкинский городской суд",
            "case_number": "2-1234/2015",
            "act_date": "10.03.2015",
            "doc_type": "Исполнительный лист",
            "blank_number": "ФС № 002000001"
        },
        "claimant": {
            "name": "Иванов И.И.",
            "party_type": "Физлицо",
            "details": "Взыскатель"
        },
        "debtor": {
            "name": "Администрация ГО Химки",
            "party_type": "Муниципальный орган",
            "details": "МО, г. Химки"
        },
        "claim_subject": "Сформировать земельный участок",
        "finances": {
            "total_rub": None
        },
        "has_signature_stamp": True
    }

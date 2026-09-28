# -*- coding: utf-8 -*-
"""
Загрузка окружения и пути проекта (C-13).

Фаза 8.10: этот модуль существует, чтобы .env читался ДО того, как какой-либо
другой модуль пакета прочитает переменные окружения на уровне модуля.
Раньше загрузка стояла в середине facade.py, между блоками импортов, и
pyproject.toml подавлял E402 для всего файла.

Порядок поиска .env (первый найденный побеждает, переменные не перезатираются):
  1. каталог, заданный SCANREADER_HOME;
  2. текущий рабочий каталог — основной случай при запуске из репозитория;
  3. корень проекта (на два уровня выше src/scan_reader) — запуск из установленного пакета.
"""

from __future__ import annotations

import os
from typing import List

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - python-dotenv в обязательных зависимостях
    def load_dotenv(*_args, **_kwargs) -> bool:  # type: ignore[misc]
        return False


SRC_DIR = os.path.dirname(os.path.abspath(__file__))
PACKAGE_DIR = os.path.abspath(os.path.join(SRC_DIR, ".."))
PROJECT_ROOT = os.path.abspath(os.path.join(PACKAGE_DIR, ".."))


def _dotenv_candidates() -> List[str]:
    home = os.getenv("SCANREADER_HOME")
    paths = []
    if home:
        paths.append(os.path.join(home, ".env"))
    paths.append(os.path.join(os.getcwd(), ".env"))
    paths.append(os.path.join(PROJECT_ROOT, ".env"))
    return paths


def load_environment() -> None:
    """Загружает .env из первого существующего кандидата. Идемпотентна."""
    for candidate in _dotenv_candidates():
        if os.path.exists(candidate):
            load_dotenv(candidate, override=False)
            return


load_environment()

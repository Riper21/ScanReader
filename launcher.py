# -*- coding: utf-8 -*-
"""
Корневой лаунчер ScanReader — шим на модульный пакет src/scan_reader.

Поддерживаемые сценарии:
- Двойной клик / интерактивное меню:        py -3 launcher.py
- Drag & Drop файла или папки:              py -3 launcher.py "C:\Сканы\документ.pdf"
- Диагностика VLM:                          py -3 launcher.py --diagnostics
- Бенчмарк Ground Truth:                     py -3 launcher.py --benchmark
- Пересборка реестров и Excel:               py -3 launcher.py --excel
"""

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from scan_reader.launcher import main  # noqa: E402

if __name__ == "__main__":
    main()

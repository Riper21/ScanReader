# -*- coding: utf-8 -*-
"""
ScanReader CLI entry point — шим на модульный пакет scan_reader.cli.

Поддерживает все подкоманды: run, classify, verify, export, benchmark, doctor, mcp
и обратную совместимость вида `py -3 cli.py <файл>` (эквивалент `run`).
"""

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from scan_reader.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""Правило 1 AGENTS.md: нулевая терпимость к «тихим сбоям».

Паттерн ``except: pass`` запрещён: любая ошибка обязана попадать в лог
(``get_logger``) либо в возвращаемую структуру (``status: FAILED``,
``errors: [...]``), не прерывая остальную обработку. Сканер обходит AST
каждого модуля пакета, чтобы паттерн не вернулся незамеченным — руками
выискивать «проглатывания» по диффам ненадёжно.

См. также test_phase1_verification_honesty.py — семантика честных статусов.
"""

import ast
from pathlib import Path

SRC_ROOT = Path(__file__).resolve().parents[1] / "src" / "scan_reader"


def _is_silent_handler(handler: ast.ExceptHandler) -> bool:
    """except-блок из единственного ``pass`` (или ``...``) — тихий сбой."""
    if len(handler.body) != 1:
        return False
    stmt = handler.body[0]
    if isinstance(stmt, ast.Pass):
        return True
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and stmt.value.value is Ellipsis
    )


def test_no_silent_except_pass_in_package() -> None:
    violations: list = []
    for path in sorted(SRC_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and _is_silent_handler(node):
                rel = path.relative_to(SRC_ROOT)
                violations.append(f"{rel}:{node.lineno}")
    assert not violations, (
        "Запрещённый паттерн «тихий сбой» (Правило 1 AGENTS.md): "
        f"{violations}. Логируйте ошибку или изолируйте её в возвращаемой структуре."
    )

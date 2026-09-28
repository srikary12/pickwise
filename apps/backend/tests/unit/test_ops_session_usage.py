# SPDX-License-Identifier: AGPL-3.0-only
"""ops_session() is callable only from @ops_task / @ops_command functions (CLAUDE.md rule 4).

The runtime guard in shared/db.py catches misuse when it runs; this test catches it
in code that no test exercises.
"""

import ast
from pathlib import Path

import pickwise

SRC = Path(pickwise.__file__).resolve().parent
ALLOWED_DECORATORS = {"ops_task", "ops_command"}


def _decorator_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = set()
    for dec in func.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(target, ast.Name):
            names.add(target.id)
        elif isinstance(target, ast.Attribute):
            names.add(target.attr)
    return names


def _violations(tree: ast.AST, filename: str) -> list[str]:
    found: list[str] = []

    def walk(node: ast.AST, enclosing: list[ast.FunctionDef | ast.AsyncFunctionDef]) -> None:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            enclosing = [*enclosing, node]
        if isinstance(node, ast.Call):
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", None)
            # A nested helper inherits its decorated parent's scope at runtime.
            allowed = any(_decorator_names(f) & ALLOWED_DECORATORS for f in enclosing)
            if name == "ops_session" and not allowed:
                where = enclosing[-1].name if enclosing else "<module>"
                found.append(f"{filename}:{node.lineno} in {where}")
        for child in ast.iter_child_nodes(node):
            walk(child, enclosing)

    walk(tree, [])
    return found


def test_ops_session_is_only_used_by_ops_entry_points() -> None:
    violations = []
    for path in SRC.rglob("*.py"):
        if path.name == "db.py" and path.parent.name == "shared":
            continue  # the definition itself
        violations += _violations(
            ast.parse(path.read_text(encoding="utf-8")), str(path.relative_to(SRC))
        )
    assert violations == [], "ops_session() outside @ops_task/@ops_command:\n" + "\n".join(
        violations
    )


def test_the_checker_catches_an_undecorated_call() -> None:
    bad = ast.parse("async def f(db):\n    async with db.ops_session():\n        pass\n")
    good = ast.parse(
        "@ops_task\nasync def f(db):\n    async def inner():\n        async with db.ops_session():\n"
        "            pass\n"
    )
    assert _violations(bad, "bad.py") == ["bad.py:2 in f"]
    assert _violations(good, "good.py") == []

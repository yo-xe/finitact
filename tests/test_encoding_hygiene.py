"""Structural guard for BUG-0007: an unspecified encoding falls back to the OS locale, which
silently differs between WSL (UTF-8) and Windows-native Python (commonly CP932 on a Japanese
system). `Path.read_text()`/`write_text()` without `encoding=` broke import entirely on a
Windows-native interpreter reading `snapshot.js`; this test pins the whole package against a
repeat rather than just the three call sites that happened to be hit."""

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[1] / "finitact"


def test_read_text_and_write_text_calls_specify_an_explicit_encoding():
    offenders = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in {"read_text", "write_text"}
                and not any(kw.arg == "encoding" for kw in node.keywords)
            ):
                offenders.append(f"{path.relative_to(PACKAGE_ROOT.parent)}:{node.lineno}")
    assert not offenders, f"read_text/write_text without explicit encoding=: {offenders}"

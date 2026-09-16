"""Regression gates for standalone repository portability."""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUFFIXES = {".py", ".toml", ".txt", ".yml", ".yaml", ".ps1", ".sh"}
PATTERNS = (
    re.compile(r"\.\.[/\\]collector_[a-z0-9_]+", re.IGNORECASE),
    re.compile(r"(?:^|\s)-e\s+\.\.[/\\]", re.IGNORECASE),
    re.compile(r"C:\\Users\\|/Users/|/home/[A-Za-z0-9_.-]+/", re.IGNORECASE),
)


def files() -> list[Path]:
    return [
        p
        for p in ROOT.rglob("*")
        if p.is_file()
        and ".git" not in p.parts
        and ".venv" not in p.parts
        and p.name != Path(__file__).name
        and p.suffix.lower() in SUFFIXES
    ]


def test_no_cross_repository_python_imports() -> None:
    found: list[str] = []
    for path in files():
        if path.suffix != ".py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"), filename=str(path))):
            names = (
                [a.name for a in node.names]
                if isinstance(node, ast.Import)
                else ([node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
            )
            found.extend(
                f"{path.relative_to(ROOT)}:{getattr(node, 'lineno', 0)}:{name}"
                for name in names
                if name.startswith("collector_")
            )
    assert not found, "Cross-repository imports found: " + "; ".join(found)


def test_no_sibling_paths_editable_installs_or_personal_absolute_paths() -> None:
    found = [
        f"{p.relative_to(ROOT)} matches {pattern.pattern}"
        for p in files()
        for pattern in PATTERNS
        if pattern.search(p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not found, "Non-portable references found: " + "; ".join(found)


def test_repository_contains_no_symlinks() -> None:
    links = [
        str(p.relative_to(ROOT))
        for p in ROOT.rglob("*")
        if p.is_symlink() and ".venv" not in p.parts
    ]
    assert not links, f"Repository symlinks are forbidden: {links}"


def test_public_imports_are_side_effect_free_without_runtime_configuration() -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("DATABRICKS_")}
    for key in (
        "PROD",
        "COLLECTOR_DB_URL",
        "DBX_SERVER_HOSTNAME",
        "DBX_HTTP_PATH",
        "AKV_VAULT_URL",
    ):
        env.pop(key, None)
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(ROOT)})
    with tempfile.TemporaryDirectory() as directory:
        run = subprocess.run(
            [sys.executable, "-c", "import scripts.config, scripts.db, main"],
            cwd=directory,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    assert run.returncode == 0, run.stderr

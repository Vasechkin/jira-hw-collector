#!/usr/bin/env python3
"""Проверка, что в Git не подготовлены локальные данные и секреты."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BANNED = re.compile(r"(^|/)(config\.toml|\.env|output|hardware-collection|hardware-collection-archives)(/|$)")
SECRET = re.compile(r"(?i)(bearer\s+[a-z0-9._-]{16,}|AGE-SECRET-KEY-|JIRA_TOKEN\s*=\s*[^у\s][^\s]+)")


def main() -> int:
    tracked = subprocess.run(["git", "ls-files"], cwd=ROOT, text=True, capture_output=True, check=True).stdout.splitlines()
    errors = [f"Запрещённый путь в Git: {name}" for name in tracked if BANNED.search(name)]
    for name in tracked:
        if name == "tools/check_repository_safety.py":
            continue
        path = ROOT / name
        if path.is_file() and path.stat().st_size <= 2_000_000:
            try:
                content = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if SECRET.search(content):
                errors.append(f"Возможный секрет: {name}")
    if errors:
        print("\n".join(errors), file=sys.stderr); return 1
    print("Проверка безопасности репозитория: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

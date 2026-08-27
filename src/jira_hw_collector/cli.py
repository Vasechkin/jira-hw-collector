"""Командная строка коллектора оборудования Jira."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

from .client import JiraClient
from .collector import collect, collect_selected_attachments
from .config import ConfigError, load_config


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(prog="jira-hw-collector", description="Полный сбор реестра оборудования из Jira")
    result.add_argument("--config", default="config.toml", help="общий локальный config.toml Jira")
    result.add_argument("command", choices=("validate", "probe", "collect", "download-attachments"), nargs="?", default="collect")
    result.add_argument("--selection-csv", help="CSV с колонками key и filename или id для целевой загрузки")
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        config = load_config(args.config)
        if config.jira.verify is False:
            print("Предупреждение: проверка сертификата TLS Jira отключена", file=sys.stderr)
        if args.command == "validate":
            print("Конфигурация корректна")
            return 0
        if args.command == "probe":
            client = JiraClient(config.jira)
            metadata = client.metadata(config.hardware.project_key)
            keys = list(client.iter_issue_keys(config.hardware.jql))
            print(json.dumps({"оборудование": len(keys), "поля": len(metadata.get("fields") or [])}, ensure_ascii=False))
            return 0
        if args.command == "download-attachments":
            if not args.selection_csv:
                raise ConfigError("Для download-attachments требуется --selection-csv")
            archive, manifest = collect_selected_attachments(config, Path(args.selection_csv).expanduser().resolve())
            print(f"Запрошено вложений: {manifest['requested_attachments']}")
            print(f"Загружено вложений: {manifest['downloaded_attachments']}")
            print(f"Ошибок загрузки: {manifest['download_errors']}")
            print(f"Результат: {archive}")
            return 0 if not manifest["download_errors"] and not manifest["missing"] else 2
        archive, manifest = collect(config)
        print(f"Получено объектов оборудования: {manifest['equipment']}")
        print(f"Ошибок: {manifest['failed_equipment']}")
        print(f"Результат: {archive}")
        return 0 if manifest["failed_equipment"] == 0 else 2
    except (ConfigError, requests.RequestException, ValueError, OSError, RuntimeError) as error:
        print(f"Ошибка: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

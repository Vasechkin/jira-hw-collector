"""Загрузка общей конфигурации Jira и параметров проекта оборудования."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv


class ConfigError(ValueError):
    """Ошибка локальной конфигурации."""


@dataclass(frozen=True)
class JiraConfig:
    base_url: str
    api_path: str
    token: str
    proxy: str | None
    verify: bool | str
    timeout_seconds: float
    page_size: int


@dataclass(frozen=True)
class HardwareConfig:
    project_key: str
    jql: str
    detail_workers: int
    best_effort: bool
    collect_comments: bool
    collect_worklogs: bool
    collect_changelog: bool
    collect_remote_links: bool
    collect_properties: bool
    collect_watchers: bool
    collect_votes: bool
    download_attachments: bool
    max_attachment_bytes: int


@dataclass(frozen=True)
class OutputConfig:
    directory: Path
    json_filename: str
    csv_filename: str
    fields_filename: str
    links_filename: str
    attachments_filename: str
    changelog_filename: str
    manifest_filename: str


@dataclass(frozen=True)
class ArchiveConfig:
    enabled: bool
    recipient: str
    identity_file: Path | None
    delete_plaintext_after_success: bool


@dataclass(frozen=True)
class Config:
    jira: JiraConfig
    hardware: HardwareConfig
    output: OutputConfig
    archive: ArchiveConfig


def _required(section: dict[str, Any], key: str) -> Any:
    value = section.get(key)
    if value is None or value == "":
        raise ConfigError(f"Не задан обязательный параметр конфигурации: {key}")
    return value


def _url(value: str, label: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigError(f"{label} должен быть абсолютным HTTP(S)-адресом")
    return value.rstrip("/")


def _boolean(section: dict[str, Any], key: str, default: bool) -> bool:
    value = section.get(key, default)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} должен иметь значение true или false")
    return value


def _filename(section: dict[str, Any], key: str, default: str) -> str:
    return Path(str(section.get(key, default))).name


def load_config(path: str | Path) -> Config:
    config_path = Path(path).expanduser().resolve()
    if not config_path.is_file():
        raise ConfigError(f"Файл конфигурации не найден: {config_path}")
    load_dotenv(config_path.parent / ".env", override=False)
    with config_path.open("rb") as handle:
        raw = tomllib.load(handle)

    jira = raw.get("jira", {})
    hardware = raw.get("hardware", {})
    output = raw.get("hardware_output", {})
    archive = raw.get("hardware_archive", {})
    if not all(isinstance(item, dict) for item in (jira, hardware, output, archive)):
        raise ConfigError("Секции конфигурации должны быть таблицами TOML")

    token_env = str(_required(jira, "token_env"))
    token = os.getenv(token_env)
    if not token:
        raise ConfigError(f"Переменная окружения {token_env!r} не задана")

    proxy_value = str(jira.get("proxy", "")).strip() or None
    proxy = _url(proxy_value, "proxy") if proxy_value else None
    verify_tls = _boolean(jira, "verify_tls", True)
    ca_bundle = str(jira.get("ca_bundle", "")).strip()
    if ca_bundle:
        ca_path = Path(ca_bundle).expanduser()
        if not ca_path.is_absolute():
            ca_path = config_path.parent / ca_path
        if not ca_path.is_file():
            raise ConfigError(f"Не найден файл сертификатов TLS: {ca_path}")
        verify: bool | str = str(ca_path.resolve())
    else:
        verify = verify_tls

    project_key = str(_required(hardware, "project_key")).strip()
    jql = str(_required(hardware, "jql")).strip()
    page_size = int(jira.get("page_size", 100))
    if not 1 <= page_size <= 1000:
        raise ConfigError("page_size должен быть от 1 до 1000")
    detail_workers = int(hardware.get("detail_workers", 4))
    if not 1 <= detail_workers <= 32:
        raise ConfigError("detail_workers должен быть от 1 до 32")
    max_attachment_bytes = int(hardware.get("max_attachment_bytes", 25 * 1024 * 1024))
    if max_attachment_bytes < 0:
        raise ConfigError("max_attachment_bytes не может быть отрицательным")

    output_dir = Path(str(output.get("directory", "hardware-collection"))).expanduser()
    if not output_dir.is_absolute():
        output_dir = config_path.parent / output_dir
    identity_value = os.path.expandvars(str(archive.get("identity_file", "")).strip())
    identity_file = Path(identity_value).expanduser().resolve() if identity_value else None

    return Config(
        jira=JiraConfig(
            base_url=_url(str(_required(jira, "base_url")), "base_url"),
            api_path="/" + str(jira.get("api_path", "/rest/api/2")).strip("/"),
            token=token,
            proxy=proxy,
            verify=verify,
            timeout_seconds=float(jira.get("timeout_seconds", 30)),
            page_size=page_size,
        ),
        hardware=HardwareConfig(
            project_key=project_key,
            jql=jql,
            detail_workers=detail_workers,
            best_effort=_boolean(hardware, "best_effort", True),
            collect_comments=_boolean(hardware, "collect_comments", True),
            collect_worklogs=_boolean(hardware, "collect_worklogs", True),
            collect_changelog=_boolean(hardware, "collect_changelog", True),
            collect_remote_links=_boolean(hardware, "collect_remote_links", True),
            collect_properties=_boolean(hardware, "collect_properties", True),
            collect_watchers=_boolean(hardware, "collect_watchers", True),
            collect_votes=_boolean(hardware, "collect_votes", True),
            download_attachments=_boolean(hardware, "download_attachments", False),
            max_attachment_bytes=max_attachment_bytes,
        ),
        output=OutputConfig(
            directory=output_dir.resolve(),
            json_filename=_filename(output, "json_filename", "equipment.json"),
            csv_filename=_filename(output, "csv_filename", "equipment.csv"),
            fields_filename=_filename(output, "fields_filename", "field_catalog.csv"),
            links_filename=_filename(output, "links_filename", "issue_links.csv"),
            attachments_filename=_filename(output, "attachments_filename", "attachments.csv"),
            changelog_filename=_filename(output, "changelog_filename", "changelog.csv"),
            manifest_filename=_filename(output, "manifest_filename", "manifest.json"),
        ),
        archive=ArchiveConfig(
            enabled=_boolean(archive, "enabled", True),
            recipient=str(archive.get("recipient", "")).strip(),
            identity_file=identity_file,
            delete_plaintext_after_success=_boolean(archive, "delete_plaintext_after_success", True),
        ),
    )

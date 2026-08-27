"""Оркестрация полного снимка оборудования."""

from __future__ import annotations

import hashlib
import csv
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import JiraClient
from .config import Config
from .exporters import BASE_COLUMNS, changelog_rows, dynamic_columns, issue_row, link_rows, text, write_csv, write_json
from .security import archive_run, private_directory, redact


def _load_attachment_selection(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"Файл выбора вложений не найден: {path}")
    with path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    selected = []
    for number, row in enumerate(rows, 2):
        key = str(row.get("key") or "").strip()
        filename = str(row.get("filename") or "").strip()
        attachment_id = str(row.get("id") or "").strip()
        if not key or (not filename and not attachment_id):
            raise ValueError(f"Строка {number}: нужны key и filename или id")
        selected.append({"key": key, "filename": filename, "id": attachment_id})
    if not selected:
        raise ValueError("Файл выбора вложений пуст")
    return selected


def collect_selected_attachments(config: Config, selection_path: Path) -> tuple[Path, dict[str, Any]]:
    """Загрузить только явно перечисленные вложения Jira и зашифровать результат."""
    os.umask(0o077)
    selection = _load_attachment_selection(selection_path)
    root = config.output.directory
    private_directory(root)
    run_name = "attachments-" + datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = root / run_name
    attachment_dir = run_dir / "attachments"
    private_directory(attachment_dir)

    by_key: dict[str, list[dict[str, str]]] = {}
    for item in selection:
        by_key.setdefault(item["key"], []).append(item)

    client = JiraClient(config.jira)
    rows: list[dict[str, str]] = []
    found: set[tuple[str, str, str]] = set()
    for key in sorted(by_key):
        issue = client.issue(key)
        attachments = (issue.get("fields") or {}).get("attachment") or []
        for wanted in by_key[key]:
            matches = [
                item for item in attachments
                if (wanted["id"] and str(item.get("id") or "") == wanted["id"])
                or (wanted["filename"] and str(item.get("filename") or "") == wanted["filename"])
            ]
            for attachment in matches:
                attachment_id = text(attachment.get("id"))
                filename = text(attachment.get("filename"))
                identity = (key, attachment_id, filename)
                if identity in found:
                    continue
                found.add(identity)
                row = {
                    "key": key, "id": attachment_id, "filename": filename,
                    "size": text(attachment.get("size")), "mime_type": text(attachment.get("mimeType")),
                    "created": text(attachment.get("created")), "author": text(attachment.get("author")),
                    "content_url": text(attachment.get("content")), "local_file": "", "sha256": "", "error": "",
                }
                safe_name = Path(filename or attachment_id or "attachment").name
                target_dir = attachment_dir / key
                private_directory(target_dir)
                target = target_dir / f"{attachment_id or 'file'}-{safe_name}"
                try:
                    _, digest = client.download(str(attachment.get("content") or ""), target, config.hardware.max_attachment_bytes)
                    row["local_file"] = str(target.relative_to(run_dir))
                    row["sha256"] = digest
                except Exception as error:
                    target.unlink(missing_ok=True)
                    row["error"] = f"{type(error).__name__}: {error}"
                rows.append(redact(row))

    missing = [
        item for item in selection
        if not any(key == item["key"] and (not item["id"] or attachment_id == item["id"])
                   and (not item["filename"] or filename == item["filename"])
                   for key, attachment_id, filename in found)
    ]
    write_csv(rows, ["key", "id", "filename", "size", "mime_type", "created", "author", "content_url", "local_file", "sha256", "error"], run_dir / config.output.attachments_filename)
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "collector": "jira-hw-collector", "mode": "selected-attachments",
        "project": config.hardware.project_key,
        "requested_attachments": len(selection), "matched_attachments": len(found),
        "downloaded_attachments": sum(bool(row["local_file"]) for row in rows),
        "download_errors": sum(bool(row["error"]) for row in rows),
        "missing": missing,
        "files": sorted(path.name for path in run_dir.iterdir() if path.is_file()),
    }
    write_json(manifest, run_dir / config.output.manifest_filename)
    return archive_run(run_dir, config.archive), manifest


def _collect_one(config: Config, key: str) -> dict[str, Any]:
    client = JiraClient(config.jira)
    issue = client.issue(key)
    related = client.related(
        key,
        comments=config.hardware.collect_comments,
        worklogs=config.hardware.collect_worklogs,
        changelog=config.hardware.collect_changelog,
        remote_links=config.hardware.collect_remote_links,
        properties=config.hardware.collect_properties,
        watchers=config.hardware.collect_watchers,
        votes=config.hardware.collect_votes,
    )
    return {"issue": issue, "related": related, "errors": []}


def collect(config: Config) -> tuple[Path, dict[str, Any]]:
    os.umask(0o077)
    root = config.output.directory
    private_directory(root)
    run_name = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")
    run_dir = root / run_name
    private_directory(run_dir)

    client = JiraClient(config.jira)
    metadata = client.metadata(config.hardware.project_key)
    keys = list(client.iter_issue_keys(config.hardware.jql))
    items: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    with ThreadPoolExecutor(max_workers=config.hardware.detail_workers) as executor:
        futures = {executor.submit(_collect_one, config, key): key for key in keys}
        for completed, future in enumerate(as_completed(futures), 1):
            key = futures[future]
            try:
                items.append(future.result())
            except Exception as error:
                failures.append({"key": key, "error": f"{type(error).__name__}: {error}"})
                if not config.hardware.best_effort:
                    raise
            if completed % 50 == 0 or completed == len(keys):
                print(f"Обработано карточек: {completed}/{len(keys)}; ошибок: {len(failures)}", flush=True)
    items.sort(key=lambda item: str((item.get("issue") or {}).get("key") or ""))

    attachment_rows: list[dict[str, str]] = []
    attachment_dir = run_dir / "attachments"
    if config.hardware.download_attachments:
        private_directory(attachment_dir)
    for item in items:
        issue = item.get("issue") or {}; key = str(issue.get("key") or "")
        for attachment in (issue.get("fields") or {}).get("attachment") or []:
            row = {"key": key, "id": text(attachment.get("id")), "filename": text(attachment.get("filename")),
                   "size": text(attachment.get("size")), "mime_type": text(attachment.get("mimeType")),
                   "created": text(attachment.get("created")), "author": text(attachment.get("author")),
                   "content_url": text(attachment.get("content")), "local_file": "", "sha256": "", "error": ""}
            if config.hardware.download_attachments and attachment.get("content"):
                safe_name = Path(str(attachment.get("filename") or attachment.get("id") or "attachment")).name
                target_dir = attachment_dir / key; private_directory(target_dir)
                target = target_dir / f"{attachment.get('id', 'file')}-{safe_name}"
                try:
                    _, digest = client.download(str(attachment["content"]), target, config.hardware.max_attachment_bytes)
                    row["local_file"] = str(target.relative_to(run_dir)); row["sha256"] = digest
                except Exception as error:
                    target.unlink(missing_ok=True); row["error"] = f"{type(error).__name__}: {error}"
            attachment_rows.append(redact(row))

    safe_metadata = redact(metadata)
    safe_items = redact(items)
    fields = safe_metadata.get("fields") if isinstance(safe_metadata.get("fields"), list) else []
    dynamic = dynamic_columns(fields)
    write_json({"metadata": safe_metadata, "equipment": safe_items, "failures": failures}, run_dir / config.output.json_filename)
    equipment_columns = [*BASE_COLUMNS, *(column for column, _ in dynamic)]
    write_csv([issue_row(item, dynamic) for item in safe_items], equipment_columns, run_dir / config.output.csv_filename)
    write_csv([{k: text(v) for k, v in field.items()} for field in fields], ["id", "name", "custom", "orderable", "navigable", "searchable", "clauseNames", "schema"], run_dir / config.output.fields_filename)
    write_csv(link_rows(safe_items), ["source", "direction", "type", "target"], run_dir / config.output.links_filename)
    write_csv(attachment_rows, ["key", "id", "filename", "size", "mime_type", "created", "author", "content_url", "local_file", "sha256", "error"], run_dir / config.output.attachments_filename)
    write_csv(changelog_rows(safe_items), ["key", "history_id", "created", "author", "field", "field_id", "from", "to"], run_dir / config.output.changelog_filename)
    manifest = {
        "created_utc": datetime.now(timezone.utc).isoformat(), "collector": "jira-hw-collector",
        "project": config.hardware.project_key, "jql_sha256": hashlib.sha256(config.hardware.jql.encode()).hexdigest(),
        "equipment": len(items), "failed_equipment": len(failures), "field_catalog": len(fields),
        "attachments": len(attachment_rows), "downloaded_attachments": sum(bool(x["local_file"]) for x in attachment_rows),
        "files": sorted(p.name for p in run_dir.iterdir() if p.is_file()),
    }
    write_json(manifest, run_dir / config.output.manifest_filename)
    return archive_run(run_dir, config.archive), manifest

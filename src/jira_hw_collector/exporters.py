"""Экспорт сырого снимка и нормализованных таблиц для анализа."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


BASE_COLUMNS = (
    "key", "summary", "issue_type", "status", "resolution", "priority", "created", "updated",
    "assignee", "reporter", "creator", "components", "labels", "security", "description",
)


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, dict):
        for key in ("displayName", "name", "value", "key"):
            if value.get(key) not in (None, ""):
                return str(value[key])
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _name(value: Any) -> str:
    return text(value) if isinstance(value, dict) else ""


def dynamic_columns(fields: list[dict[str, Any]]) -> list[tuple[str, str]]:
    result = []
    seen = set(BASE_COLUMNS)
    for field in fields:
        field_id = str(field.get("id", ""))
        if not field_id:
            continue
        title = str(field.get("name") or field_id).strip().replace("\n", " ")
        column = f"{title} [{field_id}]"
        if column not in seen:
            result.append((column, field_id)); seen.add(column)
    return result


def issue_row(item: dict[str, Any], dynamic: list[tuple[str, str]]) -> dict[str, str]:
    issue = item.get("issue") or {}
    fields = issue.get("fields") or {}
    row = {
        "key": str(issue.get("key") or ""),
        "summary": text(fields.get("summary")),
        "issue_type": _name(fields.get("issuetype")),
        "status": _name(fields.get("status")),
        "resolution": _name(fields.get("resolution")),
        "priority": _name(fields.get("priority")),
        "created": text(fields.get("created")),
        "updated": text(fields.get("updated")),
        "assignee": _name(fields.get("assignee")),
        "reporter": _name(fields.get("reporter")),
        "creator": _name(fields.get("creator")),
        "components": "; ".join(_name(x) for x in fields.get("components") or []),
        "labels": "; ".join(str(x) for x in fields.get("labels") or []),
        "security": _name(fields.get("security")),
        "description": text(fields.get("description")),
    }
    for column, field_id in dynamic:
        row[column] = text(fields.get(field_id))
    return row


def write_json(value: Any, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.chmod(0o600); temporary.replace(path); path.chmod(0o600)


def write_csv(rows: list[dict[str, Any]], columns: list[str], path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
    temporary.chmod(0o600); temporary.replace(path); path.chmod(0o600)


def link_rows(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for item in items:
        issue = item.get("issue") or {}; source = str(issue.get("key") or "")
        for link in (issue.get("fields") or {}).get("issuelinks") or []:
            for direction, side in (("outward", "outwardIssue"), ("inward", "inwardIssue")):
                target = link.get(side)
                if isinstance(target, dict):
                    rows.append({"source": source, "direction": direction, "type": text(link.get("type")), "target": str(target.get("key") or "")})
        remote_links = (item.get("related") or {}).get("remote_links") or []
        for remote in remote_links if isinstance(remote_links, list) else []:
            rows.append({"source": source, "direction": "remote", "type": text(remote.get("relationship")), "target": text(remote.get("object"))})
    return rows


def changelog_rows(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows = []
    for item in items:
        key = str((item.get("issue") or {}).get("key") or "")
        histories = (item.get("related") or {}).get("changelog") or []
        for history in histories if isinstance(histories, list) else []:
            for change in history.get("items") or []:
                rows.append({"key": key, "history_id": text(history.get("id")), "created": text(history.get("created")),
                             "author": _name(history.get("author")), "field": text(change.get("field")),
                             "field_id": text(change.get("fieldId")), "from": text(change.get("fromString")), "to": text(change.get("toString"))})
    return rows

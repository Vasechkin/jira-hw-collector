#!/usr/bin/env python3
"""Сопоставление Jira HW с сетевым и OpenStack-инвентарём."""

from __future__ import annotations

import argparse
import csv
import ipaddress
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


FIELD = {
    "hostname": "customfield_13200",
    "organization": "customfield_10701",
    "kind": "customfield_10607",
    "system": "customfield_10201",
    "ip": "customfield_10444",
    "serial": "customfield_10419",
    "inventory": "customfield_10420",
    "location": "customfield_10425",
    "model": "customfield_10452",
    "balance": "customfield_10449",
}
IP_RE = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
ATTACHMENT_HINT = re.compile(
    r"(?i)(схем|diagram|topolog|паспорт|passport|спецификац|spec|инвентар|inventory|rack|стойк|config|конфиг|порт|port|serial|серийн)"
)


def scalar(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value).strip()
    if isinstance(value, dict):
        for key in ("value", "name", "displayName", "key"):
            if value.get(key) not in (None, ""):
                return str(value[key]).strip()
    if isinstance(value, list):
        return "; ".join(filter(None, (scalar(item) for item in value)))
    return str(value).strip()


def norm_host(value: Any) -> str:
    text = scalar(value).lower().strip().strip(".")
    text = re.sub(r"\s+", "", text).replace("_", "-")
    for suffix in (".tch.ru", ".integrav.ru"):
        if text.endswith(suffix):
            text = text[: -len(suffix)]
    return text


def ips(value: Any) -> set[str]:
    result = set()
    for candidate in IP_RE.findall(scalar(value)):
        try:
            result.add(str(ipaddress.ip_address(candidate)))
        except ValueError:
            pass
    return result


def read_jira(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    records = []
    attachments = []
    for item in raw.get("equipment", []):
        issue = item.get("issue") or {}; fields = issue.get("fields") or {}
        record = {
            "key": str(issue.get("key") or ""),
            "summary": scalar(fields.get("summary")),
            "issue_type": scalar(fields.get("issuetype")),
            "status": scalar(fields.get("status")),
            **{name: scalar(fields.get(field_id)) for name, field_id in FIELD.items()},
        }
        record["hostname_norm"] = norm_host(record["hostname"] or record["summary"])
        record["ips"] = ips(record["ip"])
        record["serial_norm"] = re.sub(r"[^a-z0-9]", "", record["serial"].lower())
        records.append(record)
        for attachment in fields.get("attachment") or []:
            filename = scalar(attachment.get("filename")); mime = scalar(attachment.get("mimeType"))
            reason = []
            if ATTACHMENT_HINT.search(filename): reason.append("название указывает на технические детали")
            if mime in {"application/pdf", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "application/vnd.ms-excel", "application/zip"}: reason.append("документ или архив")
            if mime.startswith("image/"): reason.append("изображение может содержать маркировку или схему")
            if reason:
                attachments.append({"key": record["key"], "filename": filename, "mime_type": mime,
                                    "size": scalar(attachment.get("size")), "reason": "; ".join(reason)})
    return records, attachments


def network_entities(path: Path) -> list[dict[str, Any]]:
    result = []
    with path.open(encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            result.append({"source_kind": "network", "organization": "Интеграв" if row["site"].startswith("INTEGRAV") else "ТКП",
                           "site": row["site"], "name": row["host"], "hostname_norm": norm_host(row["host"]),
                           "ips": ips(row.get("management")), "serial_norm": re.sub(r"[^a-z0-9]", "", row.get("serials", "").lower()),
                           "model": row.get("models", ""), "details": row})
    return result


def openstack_entities(inventory_path: Path, vm_path: Path) -> list[dict[str, Any]]:
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    vm_inventory = json.loads(vm_path.read_text(encoding="utf-8"))
    result = []
    for host in inventory.get("hosts", []):
        result.append({"source_kind": "openstack_host", "organization": "Интеграв" if host["site"].startswith("INTEGRAV") else "ТКП",
                       "site": host["site"], "name": host["host"], "hostname_norm": norm_host(host["host"]),
                       "ips": set(), "serial_norm": "", "model": "", "details": host})
    for vm in vm_inventory.get("virtual_machines", []):
        vm_ips = set()
        for interface in vm.get("interfaces", []):
            for fixed in interface.get("fixed_ips", []): vm_ips |= ips(fixed.get("ip_address"))
            vm_ips |= ips(interface.get("addresses"))
        result.append({"source_kind": "virtual_machine", "organization": "Интеграв" if vm["site"].startswith("INTEGRAV") else "ТКП",
                       "site": vm["site"], "name": vm["name"], "hostname_norm": norm_host(vm["name"]),
                       "ips": vm_ips, "serial_norm": "", "model": "", "details": vm})
    return result


def choose_matches(entity: dict[str, Any], jira: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], str]:
    serial = [item for item in jira if entity["serial_norm"] and item["serial_norm"] == entity["serial_norm"]]
    if serial: return serial, "serial"
    host = [item for item in jira if entity["hostname_norm"] and item["hostname_norm"] == entity["hostname_norm"]]
    if host: return host, "hostname"
    by_ip = [item for item in jira if entity["ips"] and item["ips"] & entity["ips"]]
    if by_ip: return by_ip, "ip"
    return [], ""


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore"); writer.writeheader(); writer.writerows(rows)
    path.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jira-json", type=Path, required=True)
    parser.add_argument("--network-csv", type=Path, required=True)
    parser.add_argument("--openstack-inventory", type=Path, required=True)
    parser.add_argument("--vm-inventory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(); args.output.mkdir(parents=True, exist_ok=True, mode=0o700); args.output.chmod(0o700)

    jira, attachments = read_jira(args.jira_json)
    entities = network_entities(args.network_csv) + openstack_entities(args.openstack_inventory, args.vm_inventory)
    rows = []; matched_keys = set()
    for entity in entities:
        matches, method = choose_matches(entity, jira)
        matched_keys.update(item["key"] for item in matches)
        rows.append({"source_kind": entity["source_kind"], "organization": entity["organization"], "site": entity["site"],
                     "inventory_name": entity["name"], "inventory_ips": "; ".join(sorted(entity["ips"])),
                     "inventory_serial": entity["details"].get("serials", "") if entity["source_kind"] == "network" else "",
                     "match_method": method, "jira_keys": "; ".join(item["key"] for item in matches),
                     "jira_hostnames": "; ".join(item["hostname"] for item in matches),
                     "jira_statuses": "; ".join(item["status"] for item in matches),
                     "organization_conflict": "; ".join(item["organization"] for item in matches if entity["organization"].lower() not in item["organization"].lower())})

    duplicates = []
    for field in ("hostname_norm", "serial_norm"):
        index = defaultdict(list)
        for item in jira:
            if item[field]: index[item[field]].append(item["key"])
        for value, keys in index.items():
            if len(keys) > 1: duplicates.append({"field": field, "value": value, "jira_keys": "; ".join(keys), "count": len(keys)})

    unmatched_jira = [item for item in jira if item["key"] not in matched_keys]
    summary = {
        "jira_equipment": len(jira), "automated_inventory": len(entities), "matched_inventory": sum(bool(row["jira_keys"]) for row in rows),
        "unmatched_inventory": sum(not row["jira_keys"] for row in rows), "matched_jira_cards": len(matched_keys),
        "unmatched_jira_cards": len(unmatched_jira), "duplicate_identifiers": len(duplicates),
        "attachment_candidates": len(attachments),
        "by_source": {kind: dict(Counter("matched" if row["jira_keys"] else "missing" for row in rows if row["source_kind"] == kind)) for kind in {row["source_kind"] for row in rows}},
        "jira_by_organization": dict(Counter(item["organization"] or "Не указана" for item in jira)),
        "unmatched_jira_by_type": dict(Counter(item["kind"] or item["issue_type"] or "Не указан" for item in unmatched_jira).most_common()),
    }
    write_csv(args.output / "inventory_matches.csv", rows, ["source_kind", "organization", "site", "inventory_name", "inventory_ips", "inventory_serial", "match_method", "jira_keys", "jira_hostnames", "jira_statuses", "organization_conflict"])
    write_csv(args.output / "jira_unmatched.csv", unmatched_jira, ["key", "summary", "issue_type", "status", "organization", "kind", "system", "hostname", "ip", "serial", "inventory", "location", "model", "balance"])
    write_csv(args.output / "jira_duplicates.csv", duplicates, ["field", "value", "jira_keys", "count"])
    write_csv(args.output / "attachment_candidates.csv", attachments, ["key", "filename", "mime_type", "size", "reason"])
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for path in args.output.iterdir(): path.chmod(0o600)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

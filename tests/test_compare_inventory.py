import json

from tools.compare_inventory import choose_matches, norm_host, read_jira, summary_host


def test_hostname_normalization_and_matching():
    jira = [{"key": "X-1", "hostname_norm": "server-01", "serial_norm": "", "ips": {"10.0.0.1"}}]
    entity = {"hostname_norm": norm_host("server-01.example.invalid"), "serial_norm": "", "ips": {"10.0.0.1"}}
    # Неизвестный доменный суффикс не отрезается, поэтому применяется точное совпадение по IP.
    matches, method = choose_matches(entity, jira)
    assert matches[0]["key"] == "X-1"
    assert method == "ip"


def test_hostname_is_extracted_from_summary_prefix():
    assert summary_host("openstack-int-02  (Б18 314(4)-14)") == "openstack-int-02"


def test_same_organization_is_preferred_for_duplicate_short_hostname():
    jira = [
        {"key": "HW-TCH", "hostname_norm": "node-01", "serial_norm": "", "ips": set(), "organization": "ТКП"},
        {"key": "HW-INT", "hostname_norm": "node-01", "serial_norm": "", "ips": set(), "organization": "Интеграв"},
    ]
    entity = {"hostname_norm": "node-01", "serial_norm": "", "ips": set(), "organization": "Интеграв"}
    matches, method = choose_matches(entity, jira)
    assert [item["key"] for item in matches] == ["HW-INT"]
    assert method == "hostname"


def test_balance_is_used_when_organization_is_empty(tmp_path):
    source = tmp_path / "equipment.json"
    source.write_text(json.dumps({"equipment": [{"issue": {"key": "HW-1", "fields": {
        "summary": "switch-01", "issuetype": {"name": "Коммутатор"},
        "status": {"name": "Установлено"}, "customfield_10701": None,
        "customfield_10449": {"value": "ТКП"}, "attachment": []
    }}}]}), encoding="utf-8")
    records, attachments = read_jira(source)
    assert records[0]["organization"] == "ТКП"
    assert attachments == []


def test_only_explicitly_technical_attachments_are_candidates(tmp_path):
    source = tmp_path / "equipment.json"
    source.write_text(json.dumps({"equipment": [{"issue": {"key": "HW-1", "fields": {
        "summary": "server-01", "issuetype": {"name": "Сервер"},
        "status": {"name": "Установлено"}, "attachment": [
            {"filename": "photo.jpg", "mimeType": "image/jpeg", "size": 1},
            {"filename": "IPMI.pdf", "mimeType": "application/pdf", "size": 2}
        ]
    }}}]}), encoding="utf-8")
    _, attachments = read_jira(source)
    assert [item["filename"] for item in attachments] == ["IPMI.pdf"]

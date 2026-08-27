from jira_hw_collector.exporters import changelog_rows, dynamic_columns, issue_row, link_rows


def test_dynamic_custom_fields_are_exported():
    fields = [{"id": "summary", "name": "Тема"}, {"id": "customfield_1", "name": "Серийный номер"}]
    dynamic = dynamic_columns(fields)
    item = {"issue": {"key": "X-1", "fields": {"summary": "Коммутатор", "customfield_1": "ABC"}}}
    row = issue_row(item, dynamic)
    assert row["Серийный номер [customfield_1]"] == "ABC"


def test_links_are_normalized():
    items = [{"issue": {"key": "X-1", "fields": {"issuelinks": [{"outwardIssue": {"key": "X-2"}, "type": {"name": "Связан"}}]}}}]
    assert link_rows(items)[0]["target"] == "X-2"


def test_expanded_changelog_is_used_when_endpoint_is_unavailable():
    items = [{"issue": {"key": "X-1", "changelog": {"histories": [{"id": "1", "created": "now", "items": [{"field": "status", "fromString": "A", "toString": "B"}]}]}}, "related": {"changelog": {"_unavailable": 404}}}]
    rows = changelog_rows(items)
    assert rows[0]["field"] == "status"
    assert rows[0]["to"] == "B"

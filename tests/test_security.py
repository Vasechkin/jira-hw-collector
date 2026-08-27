from jira_hw_collector.security import redact


def test_secret_fields_are_masked_recursively():
    value = {"name": "device", "token": "abc", "nested": {"snmpCommunity": "private"}}
    assert redact(value) == {"name": "device", "token": "<REDACTED>", "nested": {"snmpCommunity": "<REDACTED>"}}

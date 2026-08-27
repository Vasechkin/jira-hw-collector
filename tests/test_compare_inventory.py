from tools.compare_inventory import choose_matches, norm_host


def test_hostname_normalization_and_matching():
    jira = [{"key": "X-1", "hostname_norm": "server-01", "serial_norm": "", "ips": {"10.0.0.1"}}]
    entity = {"hostname_norm": norm_host("server-01.example.invalid"), "serial_norm": "", "ips": {"10.0.0.1"}}
    # Неизвестный доменный суффикс не отрезается, поэтому применяется точное совпадение по IP.
    matches, method = choose_matches(entity, jira)
    assert matches[0]["key"] == "X-1"
    assert method == "ip"

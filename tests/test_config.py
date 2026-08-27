from pathlib import Path

import pytest

from jira_hw_collector.config import ConfigError, load_config


def test_shared_config(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("JIRA_TOKEN", "test")
    identity = tmp_path / "age.key"; identity.write_text("тестовый-ключ"); identity.chmod(0o600)
    config = tmp_path / "config.toml"
    config.write_text(f'''[jira]
base_url="https://jira.invalid"
token_env="JIRA_TOKEN"
[hardware]
project_key="PROJECT"
jql="project = PROJECT"
[hardware_output]
directory="data"
[hardware_archive]
enabled=false
identity_file="{identity}"
''')
    result = load_config(config)
    assert result.hardware.project_key == "PROJECT"
    assert result.output.directory == (tmp_path / "data").resolve()


def test_missing_hardware_section(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("JIRA_TOKEN", "test")
    config = tmp_path / "config.toml"
    config.write_text('[jira]\nbase_url="https://jira.invalid"\ntoken_env="JIRA_TOKEN"\n')
    with pytest.raises(ConfigError):
        load_config(config)

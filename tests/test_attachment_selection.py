import csv

import pytest

from jira_hw_collector.collector import _load_attachment_selection


def test_attachment_selection_accepts_key_and_filename(tmp_path):
    source = tmp_path / "selection.csv"
    with source.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["key", "filename"])
        writer.writeheader()
        writer.writerow({"key": "HW-1", "filename": "IPMI.pdf"})
    assert _load_attachment_selection(source) == [{"key": "HW-1", "filename": "IPMI.pdf", "id": ""}]


def test_attachment_selection_rejects_unidentified_row(tmp_path):
    source = tmp_path / "selection.csv"
    source.write_text("key,filename\nHW-1,\n", encoding="utf-8")
    with pytest.raises(ValueError, match="filename или id"):
        _load_attachment_selection(source)

"""The migration packer reads only selected references; never opens old databases."""

import importlib.util
import json
from pathlib import Path

import pytest

from campus_inbox.legacy_bundle import load_bundle, validate

spec = importlib.util.spec_from_file_location(
    "pack_legacy", Path(__file__).resolve().parents[2] / "scripts/pack_legacy.py"
)


def module():
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


def setup(tmp_path):
    directory = tmp_path / "old-data"
    (directory / "uploads").mkdir(parents=True)
    upload = "a" * 32
    (directory / "uploads" / (upload + ".txt")).write_bytes(b"original report")
    (directory / "access-token.txt").write_text("test-private-key")
    (directory / "inbox.sqlite3").write_bytes(b"must-not-open")
    document = {
        "version": "0.1.0",
        "messages": [
            {
                "id": "m1",
                "text": "作业通知",
                "external": {
                    "cookie": "test-private-cookie",
                    "attachments": [{"name": "报告.txt", "upload_id": upload, "text": "报告"}],
                },
            }
        ],
        "proposals": [],
        "tasks": [],
    }
    source = tmp_path / "selected-export.json"
    source.write_text(json.dumps(document), encoding="utf-8")
    return source, directory


def test_selected_originals_pack_without_credentials_or_database(tmp_path):
    source, directory = setup(tmp_path)
    output = tmp_path / "migration.zip"
    result = module().pack(source, directory, output)
    assert result == {"originals": 1, "missing": 0}
    document, originals = load_bundle(output.read_bytes())
    validate(document, originals)
    assert originals[0]["content"] == b"original report"
    assert "test-private" not in json.dumps(document)
    assert (directory / "inbox.sqlite3").read_bytes() == b"must-not-open"


def test_missing_original_is_reported_without_network_or_fake_copy(tmp_path):
    source, directory = setup(tmp_path)
    next((directory / "uploads").iterdir()).unlink()
    result = module().pack(source, directory, tmp_path / "migration.zip")
    assert result == {"originals": 0, "missing": 1}


def test_packer_never_overwrites_an_existing_output(tmp_path):
    source, directory = setup(tmp_path)
    output = tmp_path / "migration.zip"
    output.write_bytes(b"keep this existing file")
    with pytest.raises(FileExistsError):
        module().pack(source, directory, output)
    assert output.read_bytes() == b"keep this existing file"


def test_malicious_upload_identity_cannot_read_arbitrary_files(tmp_path):
    source, directory = setup(tmp_path)
    document = json.loads(source.read_text())
    document["messages"][0]["external"]["attachments"][0]["upload_id"] = "../access-token"
    source.write_text(json.dumps(document))
    result = module().pack(source, directory, tmp_path / "migration.zip")
    assert result == {"originals": 0, "missing": 1}

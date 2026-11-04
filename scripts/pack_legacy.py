"""Pack a selected legacy JSON and referenced cached originals without touching its DB."""

import argparse
import hashlib
import json
import os
import re
import zipfile
from pathlib import Path

from campus_inbox.legacy_bundle import (
    MAX_BUNDLE,
    MAX_JSON,
    MAX_ORIGINAL,
    clean,
    validate,
)


def cached_original(directory, message, attachment):
    upload = attachment.get("upload_id")
    name = attachment.get("name") or attachment.get("filename") or ""
    if not isinstance(name, str):
        return None
    if isinstance(upload, str) and re.fullmatch("[a-f0-9]{32}", upload):
        folder = directory / "uploads"
        candidate = folder / (upload + Path(name).suffix.lower())
        expected_hash = None
    else:
        external = message.get("external") or {}
        if not external.get("url") or not attachment.get("url"):
            return None
        key = hashlib.sha256(
            json.dumps(
                [external["url"], attachment["url"], name],
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        folder = directory / "attachments"
        if not folder.resolve().is_relative_to(directory.resolve()):
            return None
        candidate = folder / (key + ".bin")
        manifest = folder / (key + ".json")
        if not manifest.is_file() or manifest.resolve().parent != folder.resolve():
            return None
        if manifest.stat().st_size > 4096:
            return None
        try:
            expected_hash = json.loads(manifest.read_text(encoding="utf-8"))["sha256"]
        except (OSError, ValueError, KeyError, TypeError):
            return None
    if (
        not candidate.is_file()
        or candidate.resolve().parent != folder.resolve()
        or not folder.resolve().is_relative_to(directory.resolve())
        or not 0 < candidate.stat().st_size <= MAX_ORIGINAL
    ):
        return None
    data = candidate.read_bytes()
    if len(data) > MAX_ORIGINAL:
        return None
    if expected_hash and hashlib.sha256(data).hexdigest() != expected_hash:
        return None
    return data


def pack(export: Path, data_dir: Path, output: Path):
    if not 0 < export.stat().st_size <= MAX_JSON:
        raise ValueError("Export must be 1 byte to 10 MB")
    raw = json.loads(export.read_text(encoding="utf-8-sig"))
    document = clean(raw)
    summary, _ = validate(document, [])
    manifest, originals = [], []
    total = len(json.dumps(document, ensure_ascii=False).encode())
    for message in raw["messages"]:
        for index, attachment in enumerate((message.get("external") or {}).get("attachments", [])):
            data = cached_original(data_dir, message, attachment)
            if data is None:
                continue
            total += len(data)
            if total > MAX_BUNDLE - MAX_JSON or len(originals) >= 1000:
                raise ValueError("Bundle exceeds limits; split the selected export")
            name = f"originals/{len(originals)}.bin"
            manifest.append(
                {
                    "message_id": message["id"],
                    "index": index,
                    "path": name,
                    "sha256": hashlib.sha256(data).hexdigest(),
                }
            )
            originals.append((name, data))
    # Exclusive creation: never replace a user's existing export.
    with output.open("xb") as target:
        os.chmod(output, 0o600)
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("export.json", json.dumps(document, ensure_ascii=False))
            archive.writestr("originals.json", json.dumps(manifest))
            for name, data in originals:
                archive.writestr(name, data)
    return {
        "originals": len(originals),
        "missing": summary["issues"]["attachment_references"] - len(originals),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--export", required=True, type=Path, help="Explicitly selected old JSON export"
    )
    parser.add_argument("--data-dir", required=True, type=Path, help="Old data folder; read only")
    parser.add_argument(
        "--output", required=True, type=Path, help="New ZIP; must not already exist"
    )
    args = parser.parse_args()
    try:
        print(json.dumps(pack(args.export, args.data_dir, args.output)))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        parser.exit(
            1,
            "Packing failed. Check selected paths, format and size. Source data unchanged.\n",
        )


if __name__ == "__main__":
    main()

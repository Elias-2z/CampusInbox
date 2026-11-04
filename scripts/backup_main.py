"""Back up the main SQLite database and saved attachment originals."""

import argparse
import re
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

DATABASE = "campus-inbox.db"
MARKER = "campusinbox-main-backup-v1"
MAX_BYTES = 2 * 1024**3
ATTACHMENT = re.compile(r"uploads/[A-Za-z0-9_-]{1,80}(?:\.[A-Za-z0-9]{1,16})?")


def readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def attachment_names(database):
    with closing(readonly(database)) as connection:
        connection.execute("PRAGMA trusted_schema=OFF")
        if connection.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ValueError("数据库完整性检查失败。")
        if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("数据库存在失效关联。")
        for table in ("task", "message", "suggestion", "attachment"):
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not exists:
                raise ValueError("所选数据库不是主项目数据库。")
        names = set()
        for ident, filename in connection.execute("SELECT id, filename FROM attachment"):
            name = f"uploads/{ident}{Path(filename).suffix.lower()}"
            if not ATTACHMENT.fullmatch(name):
                raise ValueError("数据库中有无法安全备份的附件名称。")
            names.add(name)
        return names


def backup(database, output):
    with tempfile.TemporaryDirectory(prefix="campusinbox-backup-") as temporary:
        snapshot = Path(temporary) / DATABASE
        with closing(readonly(database)) as source, closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
        names = attachment_names(snapshot)
        files = {name: database.parent / name for name in names}
        present = {}
        uploads = (database.parent / "uploads").resolve()
        for name, file in files.items():
            if file.is_file() and not file.is_symlink() and file.resolve().parent == uploads:
                present[name] = file
        total = snapshot.stat().st_size + sum(file.stat().st_size for file in present.values())
        if total > MAX_BYTES:
            raise ValueError("备份超过 2 GiB。")
        with output.open("xb") as stream:
            with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("format.txt", MARKER)
                archive.write(snapshot, DATABASE)
                for name, file in sorted(present.items()):
                    archive.write(file, name)
    return len(present), len(names) - len(present)


def restore(source, destination):
    if destination.exists():
        raise ValueError("请选择尚不存在的恢复目录。")
    with (
        tempfile.TemporaryDirectory(prefix="campusinbox-restore-", dir=destination.parent) as temp,
        zipfile.ZipFile(source) as archive,
    ):
        staged = Path(temp) / "restored"
        staged.mkdir()
        (staged / "uploads").mkdir()
        entries = archive.infolist()
        names = {entry.filename for entry in entries}
        if (
            len(entries) != len(names)
            or not {"format.txt", DATABASE}.issubset(names)
            or sum(entry.file_size for entry in entries) > MAX_BYTES + len(MARKER)
            or any(
                entry.filename not in {"format.txt", DATABASE}
                and not ATTACHMENT.fullmatch(entry.filename)
                for entry in entries
            )
            or archive.getinfo("format.txt").file_size != len(MARKER)
            or archive.read("format.txt") != MARKER.encode()
        ):
            raise ValueError("备份格式不正确或超过限制。")
        for name in sorted(names - {"format.txt"}):
            with archive.open(name) as origin, (staged / name).open("xb") as target:
                shutil.copyfileobj(origin, target)
        expected = attachment_names(staged / DATABASE)
        actual = names - {"format.txt", DATABASE}
        if not actual <= expected:
            raise ValueError("备份内有无法与数据库关联的附件。")
        destination.mkdir()
        moved = []
        try:
            for item in (staged / DATABASE, staged / "uploads"):
                target = destination / item.name
                item.rename(target)
                moved.append((target, item))
        except OSError:
            for target, item in reversed(moved):
                target.rename(item)
            destination.rmdir()
            raise
    return len(actual), len(expected - actual)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--database", required=True, type=Path)
    create.add_argument("--output", required=True, type=Path)
    recover = commands.add_parser("restore")
    recover.add_argument("--archive", required=True, type=Path)
    recover.add_argument("--directory", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            saved, missing = backup(args.database, args.output)
        else:
            saved, missing = restore(args.archive, args.directory)
    except (OSError, ValueError, sqlite3.Error, zipfile.BadZipFile) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"已处理 {saved} 个附件原件，缺失 {missing} 个。")


if __name__ == "__main__":
    main()

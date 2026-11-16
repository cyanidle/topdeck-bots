from __future__ import annotations

import importlib.util
import pathlib
import sqlite3
import tempfile
import unittest

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "tools" / "backup_db.py"
spec = importlib.util.spec_from_file_location("backup_db", SCRIPT)
backup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)

STAMP = 1_795_400_000


class BackupTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.db = self.root / "topbot.sqlite3"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE watchlist(id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO watchlist(name) VALUES('Bayou')")
        conn.commit()
        conn.close()
        self.out = self.root / "backups"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def read_names(self, path: pathlib.Path) -> list[str]:
        conn = sqlite3.connect(path)
        try:
            return [row[0] for row in conn.execute("SELECT name FROM watchlist ORDER BY id")]
        finally:
            conn.close()

    def test_copy_keeps_schema_and_rows(self):
        target, removed = backup.run_backup(self.db, self.out, keep=5, stamp=STAMP)
        self.assertTrue(target.is_file())
        self.assertEqual(removed, [])
        self.assertEqual(self.read_names(target), ["Bayou"])

    def test_backup_of_a_live_wal_database(self):
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("INSERT INTO watchlist(name) VALUES('Lightning Bolt')")
        conn.commit()
        try:
            target, _ = backup.run_backup(self.db, self.out, keep=5, stamp=STAMP)
        finally:
            conn.close()
        self.assertEqual(self.read_names(target), ["Bayou", "Lightning Bolt"])

    def test_name_carries_timestamp(self):
        target, _ = backup.run_backup(self.db, self.out, keep=5, stamp=STAMP)
        self.assertRegex(target.name, r"^topbot-\d{8}-\d{6}\.sqlite3$")

    def test_no_partial_file_is_left_behind(self):
        backup.run_backup(self.db, self.out, keep=5, stamp=STAMP)
        self.assertEqual([path.name for path in self.out.iterdir()], [backup.backup_name(self.db, STAMP)])

    def test_rotation_keeps_the_newest_copies(self):
        for index in range(3):
            backup.run_backup(self.db, self.out, keep=2, stamp=STAMP + index * 60)
        self.assertEqual(sorted(path.name for path in self.out.iterdir()),
                         [backup.backup_name(self.db, STAMP + 60), backup.backup_name(self.db, STAMP + 120)])

    def test_rotation_reports_removed_copies(self):
        backup.run_backup(self.db, self.out, keep=5, stamp=STAMP)
        backup.run_backup(self.db, self.out, keep=5, stamp=STAMP + 60)
        _, removed = backup.run_backup(self.db, self.out, keep=2, stamp=STAMP + 120)
        self.assertEqual([path.name for path in removed], [backup.backup_name(self.db, STAMP)])

    def test_rotation_ignores_foreign_files(self):
        self.out.mkdir(parents=True)
        foreign = self.out / "manual-restore.sqlite3"
        foreign.write_bytes(b"")
        for index in range(3):
            backup.run_backup(self.db, self.out, keep=1, stamp=STAMP + index * 60)
        self.assertTrue(foreign.is_file())
        self.assertEqual(len(list(self.out.glob("topbot-*.sqlite3"))), 1)

    def test_copy_is_readable_only_by_the_owner(self):
        target, _ = backup.run_backup(self.db, self.out, keep=5, stamp=STAMP)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_keep_zero_disables_rotation(self):
        for index in range(3):
            backup.run_backup(self.db, self.out, keep=0, stamp=STAMP + index * 60)
        self.assertEqual(len(list(self.out.iterdir())), 3)

    def test_missing_database_fails_loudly(self):
        with self.assertRaises(FileNotFoundError):
            backup.run_backup(self.root / "absent.sqlite3", self.out, keep=5, stamp=STAMP)

    def test_cli_returns_error_for_missing_database(self):
        self.assertEqual(backup.main(["--db", str(self.root / "absent.sqlite3"), "--out", str(self.out)]), 1)

    def test_cli_writes_backup(self):
        self.assertEqual(backup.main(["--db", str(self.db), "--out", str(self.out), "--keep", "5"]), 0)
        self.assertEqual(len(list(self.out.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()

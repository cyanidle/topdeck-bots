from __future__ import annotations

import asyncio
import pathlib
import sqlite3
import tempfile
import time
import unittest

from topbot.backup import HourlyBackup, hourly_path, next_run_at, run_hourly_backup
from topbot.config import Settings


class BackupFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.db_path = self.root / "data" / "topbot.sqlite3"
        self.db_path.parent.mkdir(parents=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.execute("CREATE TABLE watchlist(id INTEGER PRIMARY KEY, name TEXT)")
        self.conn.execute("INSERT INTO watchlist(name) VALUES('Bayou')")
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()
        self.tmp.cleanup()

    def names(self, path: pathlib.Path) -> list[str]:
        conn = sqlite3.connect(path)
        try:
            return [row[0] for row in conn.execute("SELECT name FROM watchlist ORDER BY id")]
        finally:
            conn.close()

    def add_name(self, name: str) -> None:
        self.conn.execute("INSERT INTO watchlist(name) VALUES(?)", (name,))
        self.conn.commit()


class HourlyPathTest(unittest.TestCase):
    def test_hourly_copy_sits_next_to_the_database(self):
        target = hourly_path(pathlib.Path("/srv/bot/data/topbot.sqlite3"))
        self.assertEqual(target, pathlib.Path("/srv/bot/data/topbot.sqlite3.hourly"))


class RunHourlyBackupTest(BackupFixture):
    def test_copy_lands_next_to_the_database_with_rows(self):
        target = run_hourly_backup(self.db_path)
        self.assertEqual(target, self.db_path.parent / "topbot.sqlite3.hourly")
        self.assertEqual(self.names(target), ["Bayou"])

    def test_copy_is_not_world_readable(self):
        target = run_hourly_backup(self.db_path)
        self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_no_partial_file_is_left_behind(self):
        run_hourly_backup(self.db_path)
        self.assertEqual(sorted(path.name for path in self.db_path.parent.iterdir()),
                         ["topbot.sqlite3", "topbot.sqlite3.hourly"])

    def test_second_run_replaces_the_same_file(self):
        first = run_hourly_backup(self.db_path)
        self.add_name("Lightning Bolt")
        second = run_hourly_backup(self.db_path)
        self.assertEqual(first, second)
        self.assertEqual(self.names(second), ["Bayou", "Lightning Bolt"])
        self.assertEqual(sorted(path.name for path in self.db_path.parent.iterdir()),
                         ["topbot.sqlite3", "topbot.sqlite3.hourly"])

    def test_live_wal_database_is_copied_with_pending_frames(self):
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.add_name("Lightning Bolt")
        target = run_hourly_backup(self.db_path)
        self.assertEqual(self.names(target), ["Bayou", "Lightning Bolt"])

    def test_missing_database_fails_loudly(self):
        absent = self.root / "absent.sqlite3"
        with self.assertRaises(FileNotFoundError):
            run_hourly_backup(absent)
        self.assertFalse(hourly_path(absent).exists())


class NextRunAtTest(unittest.TestCase):
    def test_first_run_happens_on_the_interval_border(self):
        self.assertEqual(next_run_at(1_000.0, 3600), 3600)
        self.assertEqual(next_run_at(3_599.9, 3600), 3_600)
        self.assertEqual(next_run_at(3_600.0, 3600), 7_200)

    def test_shorter_interval_uses_its_own_grid(self):
        self.assertEqual(next_run_at(1_000.0, 900), 1_800)


class DelayTest(unittest.TestCase):
    def test_delay_waits_for_the_next_border(self):
        backup = HourlyBackup(pathlib.Path("topbot.sqlite3"), 3600, clock=lambda: 7_140.0)
        self.assertEqual(backup.delay(), 60.0)

    def test_delay_never_sleeps_less_than_a_second(self):
        backup = HourlyBackup(pathlib.Path("topbot.sqlite3"), 3600, clock=lambda: 7_199.5)
        self.assertEqual(backup.delay(), 1.0)


class HourlyBackupLoopTest(BackupFixture):
    def run_task(self):
        return asyncio.run(self._expect_backup())

    async def _expect_backup(self) -> pathlib.Path:
        task = asyncio.create_task(HourlyBackup(self.db_path, 3600).run())
        try:
            for _ in range(200):
                target = hourly_path(self.db_path)
                if target.exists():
                    return target
                await asyncio.sleep(0.02)
            self.fail("hourly backup did not appear")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def test_loop_writes_a_copy_right_after_start(self):
        target = self.run_task()
        self.assertEqual(self.names(target), ["Bayou"])
        self.assertTrue(target.exists())

    def test_run_once_returns_the_written_copy(self):
        backup = HourlyBackup(self.db_path, 3600)
        target = asyncio.run(backup.run_once())
        self.assertEqual(target, hourly_path(self.db_path))
        self.assertEqual(self.names(target), ["Bayou"])


class HourlyBackupSurvivesFailureTest(unittest.TestCase):
    def test_loop_keeps_running_when_the_database_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            backup = HourlyBackup(pathlib.Path(tmp) / "absent.sqlite3", 3600)

            async def probe() -> bool:
                task = asyncio.create_task(backup.run())
                await asyncio.sleep(0.2)
                alive = not task.done()
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                return alive

            self.assertTrue(asyncio.run(probe()))


class BackupsSettingTest(unittest.TestCase):
    def test_hourly_backup_is_on_by_default(self):
        self.assertEqual(Settings().backup_interval_s, 3600)

    def test_zero_interval_turns_the_hourly_backup_off(self):
        settings = Settings.from_env(env_file="absent.env", environ={"BACKUP_INTERVAL_S": "0"})
        self.assertEqual(settings.backup_interval_s, 0)

    def test_interval_comes_from_the_environment(self):
        settings = Settings.from_env(env_file="absent.env", environ={"BACKUP_INTERVAL_S": "7200"})
        self.assertEqual(settings.backup_interval_s, 7200)


if __name__ == "__main__":
    unittest.main()

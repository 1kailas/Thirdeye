import json
import os
import unittest
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import app


class DashboardHistoryTests(unittest.TestCase):
    def test_restart_recovers_complete_archives_in_order(self):
        with TemporaryDirectory() as directory, chdir(directory), patch.dict(os.environ, PHONE_DEBUG="1"):
            self.addCleanup(app.phone_debug_records.clear)
            for i in range(4):
                record_id = f"20261101-153000-{i:03d}"
                folder = Path("debug") / record_id
                folder.mkdir(parents=True)
                meta = {"id": record_id, "time": f"2026-11-01T15:30:00.{i:03d}", "ok": True}
                (folder / "meta.json").write_text(json.dumps(meta) if i != 1 else "{")
                if i != 2:
                    (folder / "image.jpg").touch()
            app.restore_phone_history()
            self.assertEqual([r["id"][-3:] for r in app.phone_debug_records], ["000", "003"])
            with patch.dict(os.environ, PHONE_DEBUG="0"):
                app.restore_phone_history()
                self.assertFalse(app.phone_debug_records)

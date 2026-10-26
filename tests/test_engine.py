"""End-to-end integration tests for StrataLSM storage engine."""
import os
import shutil
import tempfile
import unittest
from stratalsm.engine import StrataLSM
from stratalsm.simulator import WorkloadSimulator


class TestStrataLSMEngine(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db = StrataLSM(
            db_dir=self.test_dir,
            memtable_capacity=1024,  # Small capacity to trigger flushes quickly
            max_levels=4,
            l0_file_limit=2
        )

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_end_to_end_crud(self):
        # 1. Put
        self.db.put("user:42", "Arthur Dent")
        self.db.put("planet", "Earth")

        # 2. Get
        val1 = self.db.get("user:42")
        self.assertEqual(val1, b"Arthur Dent")
        val2 = self.db.get("planet")
        self.assertEqual(val2, b"Earth")

        # 3. Overwrite
        self.db.put("planet", "Magrathea")
        self.assertEqual(self.db.get("planet"), b"Magrathea")

        # 4. Delete
        self.db.delete("user:42")
        self.assertIsNone(self.db.get("user:42"))

    def test_flush_and_compaction_recovery(self):
        # Insert enough entries to force multiple MemTable flushes and L0 compactions
        for i in range(120):
            self.db.put(f"item_{i:03d}", f"data_{i:03d}_{'x'*32}")

        # Check all entries can be retrieved from disk
        for i in range(120):
            val = self.db.get(f"item_{i:03d}")
            self.assertIsNotNone(val, f"Item {i} should be found on disk")
            self.assertTrue(val.startswith(f"data_{i:03d}".encode()))

        # Close and reopen database to test recovery from manifest and WAL
        self.db.close()

        reopened_db = StrataLSM(
            db_dir=self.test_dir,
            memtable_capacity=1024,
            max_levels=4,
            l0_file_limit=2
        )

        for i in range(120):
            val = reopened_db.get(f"item_{i:03d}")
            self.assertIsNotNone(val, f"Item {i} must survive reopen")

        reopened_db.close()

    def test_range_scan(self):
        for i in range(10):
            self.db.put(f"key_{i:02d}", f"val_{i:02d}")

        scanned = self.db.scan(start_key="key_03", end_key="key_06")
        self.assertEqual(len(scanned), 4)
        self.assertEqual(scanned[0], ("key_03", "val_03"))
        self.assertEqual(scanned[-1], ("key_06", "val_06"))

    def test_simulator_telemetry(self):
        sim = WorkloadSimulator(self.db)
        telemetry = sim.step(workload="balanced", count=15)
        self.assertIn("memtable", telemetry)
        self.assertIn("levels", telemetry)
        self.assertIn("storage", telemetry)
        self.assertIn("stats", telemetry)


if __name__ == "__main__":
    unittest.main()

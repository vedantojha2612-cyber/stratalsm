"""Unit tests for LeveledCompactor and K-way merge."""
import os
import shutil
import tempfile
import unittest
from stratalsm.models import Entry, EntryType, SSTableMeta
from stratalsm.sstable import SSTableWriter, SSTableReader
from stratalsm.compaction import LeveledCompactor


class TestCompaction(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.compactor = LeveledCompactor(
            db_dir=self.test_dir,
            max_levels=3,
            l0_file_limit=2,
            target_sstable_size=1024
        )

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_kway_merge_and_version_shadowing(self):
        p1 = os.path.join(self.test_dir, "sstables", "sst1.sst")
        w1 = SSTableWriter(p1)
        w1.append(Entry(key=b"k1", value=b"v1_old", entry_type=EntryType.PUT, timestamp=100, sequence_num=1))
        w1.append(Entry(key=b"k2", value=b"v2_old", entry_type=EntryType.PUT, timestamp=100, sequence_num=2))
        m1 = w1.finish()
        m1.level = 0

        p2 = os.path.join(self.test_dir, "sstables", "sst2.sst")
        w2 = SSTableWriter(p2)
        w2.append(Entry(key=b"k1", value=b"v1_new", entry_type=EntryType.PUT, timestamp=200, sequence_num=10))
        w2.append(Entry(key=b"k2", value=None, entry_type=EntryType.DELETE, timestamp=200, sequence_num=11))
        m2 = w2.finish()
        m2.level = 0

        levels = [[m1, m2], [], []]

        new_levels, record = self.compactor.compact_level(levels, source_level=0)

        self.assertIsNotNone(record)
        self.assertEqual(len(new_levels[0]), 0)
        self.assertGreater(len(new_levels[1]), 0)

        reader = SSTableReader(new_levels[1][0].file_path)
        e1 = reader.get(b"k1")
        self.assertIsNotNone(e1)
        self.assertEqual(e1.value, b"v1_new")

        e2 = reader.get(b"k2")
        self.assertIsNotNone(e2)
        self.assertTrue(e2.is_tombstone)
        reader.close()

    def test_tombstone_evaporation_at_bottom_level(self):
        p1 = os.path.join(self.test_dir, "sstables", "sst_evap.sst")
        w1 = SSTableWriter(p1)
        w1.append(Entry(key=b"dead_key", value=None, entry_type=EntryType.DELETE, timestamp=100, sequence_num=5))
        m1 = w1.finish()
        m1.level = 1

        levels = [[], [m1], []]
        new_levels, record = self.compactor.compact_level(levels, source_level=1)
        self.assertIsNotNone(record)
        self.assertEqual(record.tombstones_dropped, 1)
        self.assertEqual(len(new_levels[2]), 0)


if __name__ == "__main__":
    unittest.main()

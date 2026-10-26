"""Unit tests for binary SSTable read/write operations."""
import os
import shutil
import tempfile
import unittest
from stratalsm.models import Entry, EntryType
from stratalsm.sstable import SSTableWriter, SSTableReader


class TestSSTable(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.sst_path = os.path.join(self.test_dir, "test.sst")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_sstable_write_and_read(self):
        writer = SSTableWriter(self.sst_path, block_size=256)
        keys = [f"k_{i:04d}".encode() for i in range(100)]
        for idx, k in enumerate(keys):
            writer.append(Entry(key=k, value=f"val_{idx:04d}".encode(), entry_type=EntryType.PUT, timestamp=1000, sequence_num=idx))

        meta = writer.finish()
        self.assertEqual(meta.entry_count, 100)
        self.assertGreater(meta.block_count, 1)

        reader = SSTableReader(self.sst_path)
        self.assertEqual(reader.min_key, b"k_0000")
        self.assertEqual(reader.max_key, b"k_0099")

        for idx, k in enumerate(keys):
            entry = reader.get(k)
            self.assertIsNotNone(entry, f"Key {k} should be found")
            self.assertEqual(entry.value, f"val_{idx:04d}".encode())

        self.assertIsNone(reader.get(b"nonexistent_key_9999"))
        self.assertIsNone(reader.get(b"a_before_min"))
        self.assertIsNone(reader.get(b"z_after_max"))

        all_scanned = list(reader.scan())
        self.assertEqual(len(all_scanned), 100)
        self.assertEqual([e.key for e in all_scanned], keys)
        reader.close()

    def test_single_entry_sstable(self):
        single_path = os.path.join(self.test_dir, "single.sst")
        w = SSTableWriter(single_path)
        w.append(Entry(key=b"solo_key", value=b"solo_val", entry_type=EntryType.PUT, timestamp=10, sequence_num=1))
        meta = w.finish()
        self.assertEqual(meta.entry_count, 1)

        r = SSTableReader(single_path)
        self.assertTrue(r.may_contain(b"solo_key"))
        e = r.get(b"solo_key")
        self.assertIsNotNone(e)
        self.assertEqual(e.value, b"solo_val")
        r.close()


if __name__ == "__main__":
    unittest.main()

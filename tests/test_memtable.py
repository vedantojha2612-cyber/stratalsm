"""Unit tests for SkipList and MemTable."""
import unittest
from stratalsm.models import EntryType
from stratalsm.memtable import SkipList, MemTable


class TestMemTable(unittest.TestCase):
    def test_skiplist_ordering(self):
        sl = SkipList()
        # Insert random out-of-order keys
        from stratalsm.models import Entry
        keys = [b"delta", b"alpha", b"echo", b"bravo", b"charlie"]
        for idx, k in enumerate(keys):
            sl.insert(Entry(key=k, value=f"val_{idx}".encode(), entry_type=EntryType.PUT, timestamp=100, sequence_num=idx))

        sorted_keys = [k for k, _ in sl]
        self.assertEqual(sorted_keys, [b"alpha", b"bravo", b"charlie", b"delta", b"echo"])

    def test_memtable_put_get_delete(self):
        mt = MemTable(capacity_bytes=1024)
        mt.put(b"user:1", b"Alice", timestamp=10, seq=1)
        mt.put(b"user:2", b"Bob", timestamp=20, seq=2)

        e1 = mt.get(b"user:1")
        self.assertIsNotNone(e1)
        self.assertEqual(e1.value, b"Alice")

        # Tombstone delete
        mt.delete(b"user:1", timestamp=30, seq=3)
        e1_deleted = mt.get(b"user:1")
        self.assertIsNotNone(e1_deleted)
        self.assertTrue(e1_deleted.is_tombstone)

    def test_range_scan(self):
        mt = MemTable()
        for i in range(10):
            mt.put(f"key_{i:02d}".encode(), f"val_{i}".encode(), 100, i)

        scanned = list(mt.range_scan(b"key_03", b"key_07"))
        scanned_keys = [e.key for e in scanned]
        expected = [f"key_{i:02d}".encode() for i in range(3, 8)]
        self.assertEqual(scanned_keys, expected)


if __name__ == "__main__":
    unittest.main()

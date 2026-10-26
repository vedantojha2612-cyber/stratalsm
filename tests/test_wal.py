"""Unit tests for WriteAheadLog."""
import os
import shutil
import tempfile
import unittest
from stratalsm.models import Entry, EntryType
from stratalsm.wal import WriteAheadLog


class TestWriteAheadLog(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.wal_path = os.path.join(self.test_dir, "test_wal.log")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_append_and_recover(self):
        wal = WriteAheadLog(self.wal_path)
        e1 = Entry(key=b"k1", value=b"v1", entry_type=EntryType.PUT, timestamp=1000, sequence_num=1)
        e2 = Entry(key=b"k2", value=b"v2", entry_type=EntryType.PUT, timestamp=2000, sequence_num=2)
        e3 = Entry(key=b"k1", value=None, entry_type=EntryType.DELETE, timestamp=3000, sequence_num=3)

        wal.append(e1)
        wal.append(e2)
        wal.append(e3)
        wal.close()

        recovered = list(WriteAheadLog.recover(self.wal_path))
        self.assertEqual(len(recovered), 3)

        self.assertEqual(recovered[0].key, b"k1")
        self.assertEqual(recovered[0].value, b"v1")
        self.assertEqual(recovered[0].sequence_num, 1)

        self.assertEqual(recovered[1].key, b"k2")
        self.assertEqual(recovered[1].value, b"v2")

        self.assertEqual(recovered[2].key, b"k1")
        self.assertTrue(recovered[2].is_tombstone)
        self.assertIsNone(recovered[2].value)

    def test_corrupted_frame_handling(self):
        wal = WriteAheadLog(self.wal_path)
        wal.append(Entry(key=b"k1", value=b"v1", entry_type=EntryType.PUT, timestamp=100, sequence_num=1))
        wal.close()

        # Append corrupted garbage bytes to tail
        with open(self.wal_path, "ab") as f:
            f.write(b"CORRUPTED_GARBAGE_BYTES_THAT_FAIL_CRC")

        recovered = list(WriteAheadLog.recover(self.wal_path))
        self.assertEqual(len(recovered), 1)
        self.assertEqual(recovered[0].key, b"k1")

    def test_empty_or_truncated_file(self):
        # Empty file
        open(self.wal_path, "wb").close()
        recovered = list(WriteAheadLog.recover(self.wal_path))
        self.assertEqual(len(recovered), 0)

        # Truncated header (less than 12 bytes)
        with open(self.wal_path, "wb") as f:
            f.write(b"SWAL123")
        recovered = list(WriteAheadLog.recover(self.wal_path))
        self.assertEqual(len(recovered), 0)


if __name__ == "__main__":
    unittest.main()

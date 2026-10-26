"""Append-only Write-Ahead Log (WAL) with CRC32 binary framing and recovery."""
import os
import struct
import zlib
from typing import Iterator, Optional
from stratalsm.models import Entry, EntryType

WAL_MAGIC = b"SWAL"
HEADER_FORMAT = ">4sII"  # Magic (4B), Frame Length (4B), CRC32 (4B)
HEADER_SIZE = struct.calcsize(HEADER_FORMAT)
PAYLOAD_FIXED_FORMAT = ">QQBII"  # Timestamp(8B), Seq(8B), OpType(1B), KeyLen(4B), ValLen(4B)
PAYLOAD_FIXED_SIZE = struct.calcsize(PAYLOAD_FIXED_FORMAT)


class WriteAheadLog:
    def __init__(self, file_path: str, sync_on_write: bool = False):
        self.file_path = file_path
        self.sync_on_write = sync_on_write
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        self._file = open(self.file_path, "a+b")
        self.bytes_written = self._file.tell()

    def append(self, entry: Entry) -> int:
        """Appends an entry to the log with CRC32 protection. Returns bytes written."""
        val_bytes = entry.value if entry.value is not None else b""
        payload_meta = struct.pack(
            PAYLOAD_FIXED_FORMAT,
            entry.timestamp,
            entry.sequence_num,
            entry.entry_type.value,
            len(entry.key),
            len(val_bytes)
        )
        payload = payload_meta + entry.key + val_bytes
        crc = zlib.crc32(payload) & 0xFFFFFFFF
        header = struct.pack(HEADER_FORMAT, WAL_MAGIC, len(payload), crc)

        frame = header + payload
        self._file.write(frame)
        if self.sync_on_write:
            self._file.flush()
            os.fsync(self._file.fileno())

        frame_len = len(frame)
        self.bytes_written += frame_len
        return frame_len

    def flush(self):
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self):
        if not self._file.closed:
            self._file.flush()
            self._file.close()

    def clear(self):
        """Truncates the WAL after MemTable is safely persisted to SSTable."""
        self._file.close()
        self._file = open(self.file_path, "wb")
        self.bytes_written = 0

    @classmethod
    def recover(cls, file_path: str) -> Iterator[Entry]:
        """Reads and verifies all intact frames from the WAL file.
        Stops safely if a torn write or corruption is encountered at the tail."""
        if not os.path.exists(file_path):
            return

        with open(file_path, "rb") as f:
            while True:
                header_bytes = f.read(HEADER_SIZE)
                if len(header_bytes) < HEADER_SIZE:
                    break

                magic, payload_len, expected_crc = struct.unpack(HEADER_FORMAT, header_bytes)
                if magic != WAL_MAGIC:
                    # Invalid magic header, stop recovery
                    break

                payload_bytes = f.read(payload_len)
                if len(payload_bytes) < payload_len:
                    # Partial / torn frame at tail, stop recovery
                    break

                actual_crc = zlib.crc32(payload_bytes) & 0xFFFFFFFF
                if actual_crc != expected_crc:
                    # Corruption detected, stop recovery
                    break

                ts, seq, op_val, klen, vlen = struct.unpack_from(PAYLOAD_FIXED_FORMAT, payload_bytes, 0)
                offset = PAYLOAD_FIXED_SIZE
                key = payload_bytes[offset:offset + klen]
                offset += klen
                val = payload_bytes[offset:offset + vlen] if vlen > 0 else (None if op_val == EntryType.DELETE.value else b"")

                entry = Entry(
                    key=key,
                    value=val if op_val == EntryType.PUT.value else None,
                    entry_type=EntryType(op_val),
                    timestamp=ts,
                    sequence_num=seq
                )
                yield entry

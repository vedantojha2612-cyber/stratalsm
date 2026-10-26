"""Binary SSTable (Sorted String Table) implementation with 4KB block paging, sparse index, and Bloom filters."""
import os
import struct
import bisect
from typing import List, Optional, Tuple, Iterator
from stratalsm.models import Entry, EntryType, BlockMeta, SSTableMeta
from stratalsm.bloom import BloomFilter

SST_MAGIC = b"STRT_SST"
FOOTER_FORMAT = ">QQQQQ8s"  # index_offset, index_len, bloom_offset, bloom_len, total_entries, magic
FOOTER_SIZE = struct.calcsize(FOOTER_FORMAT)
DEFAULT_BLOCK_SIZE = 4096


class SSTableWriter:
    def __init__(self, file_path: str, block_size: int = DEFAULT_BLOCK_SIZE, bloom_fp_rate: float = 0.01):
        self.file_path = file_path
        self.block_size = block_size
        self.bloom_fp_rate = bloom_fp_rate
        os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
        self.file = open(self.file_path, "wb")
        self.blocks: List[BlockMeta] = []
        self.current_block_entries: List[Entry] = []
        self.current_block_bytes = 0
        self.total_entries = 0
        self.all_keys: List[bytes] = []

    def append(self, entry: Entry):
        entry_serialized_len = 8 + 8 + 1 + 2 + 4 + len(entry.key) + (len(entry.value) if entry.value else 0)
        if self.current_block_entries and (self.current_block_bytes + entry_serialized_len > self.block_size):
            self._flush_block()

        self.current_block_entries.append(entry)
        self.current_block_bytes += entry_serialized_len
        self.total_entries += 1
        self.all_keys.append(entry.key)

    def _flush_block(self):
        if not self.current_block_entries:
            return

        block_offset = self.file.tell()
        # Write entry count (2 bytes)
        self.file.write(struct.pack(">H", len(self.current_block_entries)))

        for entry in self.current_block_entries:
            val_bytes = entry.value if entry.value is not None else b""
            # [TS: 8B] [SEQ: 8B] [OP: 1B] [KLEN: 2B] [VLEN: 4B]
            header = struct.pack(
                ">QQBHI",
                entry.timestamp,
                entry.sequence_num,
                entry.entry_type.value,
                len(entry.key),
                len(val_bytes)
            )
            self.file.write(header + entry.key + val_bytes)

        block_length = self.file.tell() - block_offset
        first_key = self.current_block_entries[0].key
        last_key = self.current_block_entries[-1].key

        self.blocks.append(BlockMeta(
            offset=block_offset,
            length=block_length,
            first_key=first_key,
            last_key=last_key,
            entry_count=len(self.current_block_entries)
        ))
        self.current_block_entries = []
        self.current_block_bytes = 0

    def finish(self) -> SSTableMeta:
        self._flush_block()

        # 1. Write Sparse Index Block
        index_offset = self.file.tell()
        # Write number of blocks (4 bytes)
        self.file.write(struct.pack(">I", len(self.blocks)))
        for b in self.blocks:
            # [offset: 8B] [len: 4B] [first_klen: 2B] [first_key] [last_klen: 2B] [last_key]
            rec = struct.pack(">QIH", b.offset, b.length, len(b.first_key)) + b.first_key
            rec += struct.pack(">H", len(b.last_key)) + b.last_key
            self.file.write(rec)
        index_len = self.file.tell() - index_offset

        # 2. Build and Write Bloom Filter Block
        bloom = BloomFilter(capacity=max(10, len(self.all_keys)), error_rate=self.bloom_fp_rate)
        for k in self.all_keys:
            bloom.add(k)
        bloom_bytes = bloom.serialize()

        bloom_offset = self.file.tell()
        self.file.write(bloom_bytes)
        bloom_len = len(bloom_bytes)

        # 3. Write Footer
        footer = struct.pack(
            FOOTER_FORMAT,
            index_offset,
            index_len,
            bloom_offset,
            bloom_len,
            self.total_entries,
            SST_MAGIC
        )
        self.file.write(footer)
        self.file.flush()
        file_size = self.file.tell()
        self.file.close()

        min_k = self.all_keys[0] if self.all_keys else b""
        max_k = self.all_keys[-1] if self.all_keys else b""
        table_id = os.path.basename(self.file_path).split(".")[0]

        return SSTableMeta(
            table_id=table_id,
            level=0,
            file_path=self.file_path,
            min_key=min_k,
            max_key=max_k,
            entry_count=self.total_entries,
            file_size_bytes=file_size,
            block_count=len(self.blocks)
        )


class SSTableReader:
    def __init__(self, file_path: str):
        self.file_path = file_path
        self.file = open(self.file_path, "rb")
        self.file_size = os.path.getsize(self.file_path)

        if self.file_size < FOOTER_SIZE:
            raise ValueError(f"SSTable file {file_path} is smaller than footer size")

        # Read Footer
        self.file.seek(self.file_size - FOOTER_SIZE)
        footer_bytes = self.file.read(FOOTER_SIZE)
        (
            self.index_offset,
            self.index_len,
            self.bloom_offset,
            self.bloom_len,
            self.total_entries,
            magic
        ) = struct.unpack(FOOTER_FORMAT, footer_bytes)

        if magic != SST_MAGIC:
            self.file.close()
            raise ValueError(f"Invalid SSTable magic: {magic}")

        if (
            self.index_offset < 0
            or self.index_len < 0
            or (self.index_offset + self.index_len) > self.file_size
        ):
            self.file.close()
            raise ValueError(f"Corrupted SSTable index bounds: offset={self.index_offset}, len={self.index_len}")

        if (
            self.bloom_offset < 0
            or self.bloom_len < 0
            or (self.bloom_offset + self.bloom_len) > self.file_size
        ):
            self.file.close()
            raise ValueError(f"Corrupted SSTable bloom bounds: offset={self.bloom_offset}, len={self.bloom_len}")

        # Read Bloom Filter
        self.file.seek(self.bloom_offset)
        bloom_data = self.file.read(self.bloom_len)
        self.bloom = BloomFilter.deserialize(bloom_data)

        # Read Index
        self.file.seek(self.index_offset)
        index_data = self.file.read(self.index_len)
        num_blocks = struct.unpack_from(">I", index_data, 0)[0]
        offset = 4
        self.blocks: List[BlockMeta] = []
        for _ in range(num_blocks):
            b_off, b_len, fk_len = struct.unpack_from(">QIH", index_data, offset)
            offset += 14
            first_k = index_data[offset:offset + fk_len]
            offset += fk_len
            lk_len = struct.unpack_from(">H", index_data, offset)[0]
            offset += 2
            last_k = index_data[offset:offset + lk_len]
            offset += lk_len

            self.blocks.append(BlockMeta(
                offset=b_off,
                length=b_len,
                first_key=first_k,
                last_key=last_k,
                entry_count=0
            ))

        self.min_key = self.blocks[0].first_key if self.blocks else b""
        self.max_key = self.blocks[-1].last_key if self.blocks else b""
        self._block_first_keys = [b.first_key for b in self.blocks]

    def may_contain(self, key: bytes) -> bool:
        """Fast negative check with Bloom filter and bounding keys."""
        if not self.blocks:
            return False
        if key < self.min_key or key > self.max_key:
            return False
        return self.bloom.contains(key)

    def get(self, key: bytes) -> Optional[Entry]:
        """Looks up a key. Returns Entry or None."""
        if not self.may_contain(key):
            return None

        # Binary search block index: find rightmost block whose first_key <= key
        idx = bisect.bisect_right(self._block_first_keys, key) - 1
        if idx < 0:
            return None

        block = self.blocks[idx]
        if key > block.last_key:
            return None

        return self._search_block(block, key)

    def _search_block(self, block: BlockMeta, key: bytes) -> Optional[Entry]:
        self.file.seek(block.offset)
        block_bytes = self.file.read(block.length)
        if len(block_bytes) < 2:
            return None

        entry_count = struct.unpack_from(">H", block_bytes, 0)[0]
        offset = 2
        for _ in range(entry_count):
            if offset >= len(block_bytes):
                break
            ts, seq, op_val, klen, vlen = struct.unpack_from(">QQBHI", block_bytes, offset)
            offset += 23
            k = block_bytes[offset:offset + klen]
            offset += klen
            v = block_bytes[offset:offset + vlen] if vlen > 0 else (None if op_val == EntryType.DELETE.value else b"")
            offset += vlen

            if k == key:
                return Entry(
                    key=k,
                    value=v if op_val == EntryType.PUT.value else None,
                    entry_type=EntryType(op_val),
                    timestamp=ts,
                    sequence_num=seq
                )
            elif k > key:
                # Keys inside blocks are strictly sorted
                break

        return None

    def scan(self) -> Iterator[Entry]:
        """Yields all entries in order."""
        for block in self.blocks:
            self.file.seek(block.offset)
            block_bytes = self.file.read(block.length)
            entry_count = struct.unpack_from(">H", block_bytes, 0)[0]
            offset = 2
            for _ in range(entry_count):
                ts, seq, op_val, klen, vlen = struct.unpack_from(">QQBHI", block_bytes, offset)
                offset += 23
                k = block_bytes[offset:offset + klen]
                offset += klen
                v = block_bytes[offset:offset + vlen] if vlen > 0 else (None if op_val == EntryType.DELETE.value else b"")
                offset += vlen

                yield Entry(
                    key=k,
                    value=v if op_val == EntryType.PUT.value else None,
                    entry_type=EntryType(op_val),
                    timestamp=ts,
                    sequence_num=seq
                )

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        if hasattr(self, 'file') and not self.file.closed:
            self.file.close()

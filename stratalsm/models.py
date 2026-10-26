"""Data models and structures for StrataLSM storage engine."""
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Optional, List, Dict, Any


class EntryType(Enum):
    PUT = 1
    DELETE = 2  # Tombstone


@dataclass
class Entry:
    key: bytes
    value: Optional[bytes]
    entry_type: EntryType
    timestamp: int = field(default_factory=lambda: int(time.time() * 1000000))
    sequence_num: int = 0

    @property
    def is_tombstone(self) -> bool:
        return self.entry_type == EntryType.DELETE

    @property
    def size_bytes(self) -> int:
        return len(self.key) + (len(self.value) if self.value else 0) + 16


@dataclass
class BlockMeta:
    offset: int
    length: int
    first_key: bytes
    last_key: bytes
    entry_count: int


@dataclass
class SSTableMeta:
    table_id: str
    level: int
    file_path: str
    min_key: bytes
    max_key: bytes
    entry_count: int
    file_size_bytes: int
    block_count: int
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "table_id": self.table_id,
            "level": self.level,
            "file_path": self.file_path,
            "min_key": self.min_key.decode("utf-8", errors="replace"),
            "max_key": self.max_key.decode("utf-8", errors="replace"),
            "entry_count": self.entry_count,
            "file_size_bytes": self.file_size_bytes,
            "block_count": self.block_count,
            "created_at": self.created_at,
        }


@dataclass
class CompactionRecord:
    compaction_id: str
    source_level: int
    target_level: int
    input_files: List[str]
    output_files: List[str]
    bytes_read: int
    bytes_written: int
    tombstones_dropped: int
    duration_ms: float
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "compaction_id": self.compaction_id,
            "source_level": self.source_level,
            "target_level": self.target_level,
            "input_files": self.input_files,
            "output_files": self.output_files,
            "bytes_read": self.bytes_read,
            "bytes_written": self.bytes_written,
            "tombstones_dropped": self.tombstones_dropped,
            "duration_ms": round(self.duration_ms, 2),
            "timestamp": self.timestamp,
        }


@dataclass
class EngineStats:
    total_user_bytes_written: int = 0
    total_disk_bytes_written: int = 0
    total_puts: int = 0
    total_deletes: int = 0
    total_gets: int = 0
    memtable_hits: int = 0
    bloom_hits: int = 0
    bloom_misses: int = 0
    sstable_block_reads: int = 0
    compactions_completed: int = 0
    tombstones_evaporated: int = 0

    @property
    def write_amplification(self) -> float:
        if self.total_user_bytes_written == 0:
            return 1.0
        return round(self.total_disk_bytes_written / self.total_user_bytes_written, 2)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_user_bytes_written": self.total_user_bytes_written,
            "total_disk_bytes_written": self.total_disk_bytes_written,
            "write_amplification": self.write_amplification,
            "total_puts": self.total_puts,
            "total_deletes": self.total_deletes,
            "total_gets": self.total_gets,
            "memtable_hits": self.memtable_hits,
            "bloom_hits": self.bloom_hits,
            "bloom_misses": self.bloom_misses,
            "sstable_block_reads": self.sstable_block_reads,
            "compactions_completed": self.compactions_completed,
            "tombstones_evaporated": self.tombstones_evaporated,
        }

"""Unified StrataLSM Storage Engine coordinating WAL, MemTable, SSTables, and Compaction."""
import os
import json
import time
import uuid
import threading
from typing import Optional, List, Tuple, Dict, Any, Union
from stratalsm.models import Entry, EntryType, SSTableMeta, EngineStats, CompactionRecord
from stratalsm.wal import WriteAheadLog
from stratalsm.memtable import MemTable
from stratalsm.sstable import SSTableWriter, SSTableReader
from stratalsm.compaction import LeveledCompactor

MANIFEST_FILE = "MANIFEST.json"


class StrataLSM:
    def __init__(
        self,
        db_dir: str = "/tmp/stratalsm_data",
        memtable_capacity: int = 32 * 1024,
        max_levels: int = 4,
        l0_file_limit: int = 3,
        sync_wal: bool = False
    ):
        self.db_dir = os.path.abspath(db_dir)
        self.memtable_capacity = memtable_capacity
        self.max_levels = max_levels
        self.l0_file_limit = l0_file_limit
        self.sync_wal = sync_wal

        os.makedirs(self.db_dir, exist_ok=True)
        self.sst_dir = os.path.join(self.db_dir, "sstables")
        os.makedirs(self.sst_dir, exist_ok=True)
        self.wal_path = os.path.join(self.db_dir, "wal.log")
        self.manifest_path = os.path.join(self.db_dir, MANIFEST_FILE)

        self._lock = threading.RLock()
        self.sequence_num = 0
        self.stats = EngineStats()
        self.compaction_history: List[CompactionRecord] = []

        self.levels: List[List[SSTableMeta]] = [[] for _ in range(max_levels)]
        self.compactor = LeveledCompactor(
            db_dir=self.db_dir,
            max_levels=max_levels,
            l0_file_limit=l0_file_limit,
            target_sstable_size=memtable_capacity
        )

        self._load_manifest()

        # Initialize WAL and MemTable
        self.wal = WriteAheadLog(self.wal_path, sync_on_write=self.sync_wal)
        self.memtable = MemTable(capacity_bytes=self.memtable_capacity)
        self.immutable_memtable: Optional[MemTable] = None

        # Replay WAL if existing
        self._recover_wal()

    def _load_manifest(self):
        if not os.path.exists(self.manifest_path):
            return

        try:
            with open(self.manifest_path, "r") as f:
                data = json.load(f)
                self.sequence_num = data.get("sequence_num", 0)
                levels_data = data.get("levels", [])
                for lvl_idx, tables in enumerate(levels_data):
                    if lvl_idx < self.max_levels:
                        loaded = []
                        for t in tables:
                            if os.path.exists(t["file_path"]):
                                loaded.append(SSTableMeta(
                                    table_id=t["table_id"],
                                    level=t["level"],
                                    file_path=t["file_path"],
                                    min_key=t["min_key"].encode("utf-8"),
                                    max_key=t["max_key"].encode("utf-8"),
                                    entry_count=t["entry_count"],
                                    file_size_bytes=t["file_size_bytes"],
                                    block_count=t.get("block_count", 1),
                                    created_at=t.get("created_at", time.time())
                                ))
                        self.levels[lvl_idx] = loaded
        except Exception:
            pass

    def _save_manifest(self):
        data = {
            "sequence_num": self.sequence_num,
            "levels": [
                [m.to_dict() for m in lvl]
                for lvl in self.levels
            ]
        }
        temp_path = self.manifest_path + ".tmp"
        with open(temp_path, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_path, self.manifest_path)

    def _recover_wal(self):
        recovered_entries = list(WriteAheadLog.recover(self.wal_path))
        for entry in recovered_entries:
            if entry.sequence_num > self.sequence_num:
                self.sequence_num = entry.sequence_num
            if entry.entry_type == EntryType.PUT:
                self.memtable.put(entry.key, entry.value, entry.timestamp, entry.sequence_num)
            else:
                self.memtable.delete(entry.key, entry.timestamp, entry.sequence_num)

    def put(self, key: Union[str, bytes], value: Union[str, bytes]) -> bool:
        k = key.encode("utf-8") if isinstance(key, str) else key
        v = value.encode("utf-8") if isinstance(value, str) else value

        with self._lock:
            self.sequence_num += 1
            ts = int(time.time() * 1000000)

            # 1. Append to WAL
            entry = Entry(key=k, value=v, entry_type=EntryType.PUT, timestamp=ts, sequence_num=self.sequence_num)
            bytes_wal = self.wal.append(entry)
            self.stats.total_disk_bytes_written += bytes_wal
            self.stats.total_user_bytes_written += len(k) + len(v)
            self.stats.total_puts += 1

            # 2. Insert into MemTable
            self.memtable.put(k, v, ts, self.sequence_num)

            # 3. Check for MemTable flush threshold
            if self.memtable.is_full():
                self.flush_memtable()

            # 4. Check for background compaction
            comp_lvl = self.compactor.should_compact(self.levels)
            if comp_lvl is not None:
                self.compact_level(comp_lvl)

        return True

    def delete(self, key: Union[str, bytes]) -> bool:
        k = key.encode("utf-8") if isinstance(key, str) else key

        with self._lock:
            self.sequence_num += 1
            ts = int(time.time() * 1000000)

            entry = Entry(key=k, value=None, entry_type=EntryType.DELETE, timestamp=ts, sequence_num=self.sequence_num)
            bytes_wal = self.wal.append(entry)
            self.stats.total_disk_bytes_written += bytes_wal
            self.stats.total_user_bytes_written += len(k)
            self.stats.total_deletes += 1

            self.memtable.delete(k, ts, self.sequence_num)

            if self.memtable.is_full():
                self.flush_memtable()

            comp_lvl = self.compactor.should_compact(self.levels)
            if comp_lvl is not None:
                self.compact_level(comp_lvl)

        return True

    def get(self, key: Union[str, bytes]) -> Optional[bytes]:
        k = key.encode("utf-8") if isinstance(key, str) else key

        with self._lock:
            self.stats.total_gets += 1

            # 1. Search active MemTable
            entry = self.memtable.get(k)
            if entry is not None:
                self.stats.memtable_hits += 1
                return None if entry.is_tombstone else entry.value

            # 2. Search immutable MemTable (if flushing)
            if self.immutable_memtable:
                entry = self.immutable_memtable.get(k)
                if entry is not None:
                    self.stats.memtable_hits += 1
                    return None if entry.is_tombstone else entry.value

            # 3. Search L0 SSTables (newest to oldest, as L0 key ranges overlap)
            for meta in reversed(self.levels[0]):
                val = self._search_sstable(meta, k)
                if val is not None:
                    return None if val == b"__TOMBSTONE__" else val

            # 4. Search L1..Ln SSTables (non-overlapping per level)
            for lvl_idx in range(1, self.max_levels):
                for meta in self.levels[lvl_idx]:
                    if meta.min_key <= k <= meta.max_key:
                        val = self._search_sstable(meta, k)
                        if val is not None:
                            return None if val == b"__TOMBSTONE__" else val

            return None

    def _search_sstable(self, meta: SSTableMeta, key: bytes) -> Optional[bytes]:
        if not os.path.exists(meta.file_path):
            return None

        reader = SSTableReader(meta.file_path)
        try:
            if not reader.may_contain(key):
                self.stats.bloom_misses += 1
                return None

            self.stats.bloom_hits += 1
            self.stats.sstable_block_reads += 1
            entry = reader.get(key)
            if entry is not None:
                return b"__TOMBSTONE__" if entry.is_tombstone else entry.value
            return None
        finally:
            reader.close()

    def flush_memtable(self) -> Optional[SSTableMeta]:
        """Flushes active MemTable to a new L0 SSTable file and resets WAL."""
        with self._lock:
            if self.memtable.entry_count == 0:
                return None

            self.immutable_memtable = self.memtable
            self.memtable = MemTable(capacity_bytes=self.memtable_capacity)

            sst_id = f"sst_L0_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
            sst_path = os.path.join(self.sst_dir, f"{sst_id}.sst")
            writer = SSTableWriter(sst_path)

            for key, entry in self.immutable_memtable.items():
                writer.append(entry)

            meta = writer.finish()
            meta.level = 0
            self.levels[0].append(meta)
            self.stats.total_disk_bytes_written += meta.file_size_bytes

            # Clear WAL and reset immutable memtable
            self.wal.clear()
            self.immutable_memtable = None

            self._save_manifest()
            return meta

    def compact_level(self, source_level: int) -> Optional[CompactionRecord]:
        """Runs a compaction from source_level into target_level."""
        with self._lock:
            new_levels, record = self.compactor.compact_level(self.levels, source_level)
            if record:
                self.levels = new_levels
                self.stats.total_disk_bytes_written += record.bytes_written
                self.stats.compactions_completed += 1
                self.stats.tombstones_evaporated += record.tombstones_dropped
                self.compaction_history.append(record)
                self._save_manifest()
            return record

    def compact_all(self):
        """Forces compactions across all levels until all thresholds are satisfied."""
        with self._lock:
            if self.memtable.entry_count > 0:
                self.flush_memtable()

            while True:
                lvl = self.compactor.should_compact(self.levels)
                if lvl is None:
                    break
                rec = self.compact_level(lvl)
                if not rec:
                    break

    def scan(self, start_key: Optional[str] = None, end_key: Optional[str] = None) -> List[Tuple[str, str]]:
        """Returns all live key-values sorted lexicographically."""
        sk = start_key.encode("utf-8") if start_key else None
        ek = end_key.encode("utf-8") if end_key else None

        with self._lock:
            # Gather all entries into map key -> Entry
            records: Dict[bytes, Entry] = {}

            # Read from oldest levels up to MemTable
            for lvl in reversed(self.levels):
                for meta in lvl:
                    if os.path.exists(meta.file_path):
                        r = SSTableReader(meta.file_path)
                        for entry in r.scan():
                            if entry.key not in records or records[entry.key].sequence_num < entry.sequence_num:
                                records[entry.key] = entry
                        r.close()

            # Merge immutable and active MemTable
            if self.immutable_memtable:
                for k, entry in self.immutable_memtable.items():
                    if k not in records or records[k].sequence_num < entry.sequence_num:
                        records[k] = entry

            for k, entry in self.memtable.items():
                if k not in records or records[k].sequence_num < entry.sequence_num:
                    records[k] = entry

            # Filter live keys and range
            results = []
            for k in sorted(records.keys()):
                if sk and k < sk:
                    continue
                if ek and k > ek:
                    continue
                e = records[k]
                if not e.is_tombstone and e.value is not None:
                    results.append((k.decode("utf-8", errors="replace"), e.value.decode("utf-8", errors="replace")))

            return results

    def get_telemetry(self) -> Dict[str, Any]:
        """Provides full telemetry snapshot for 3D WebGL HUD and Cyberpunk TUI."""
        with self._lock:
            total_disk_bytes = sum(
                sum(m.file_size_bytes for m in lvl)
                for lvl in self.levels
            )
            total_entries_disk = sum(
                sum(m.entry_count for m in lvl)
                for lvl in self.levels
            )

            levels_telemetry = []
            for idx, lvl in enumerate(self.levels):
                capacity = self.compactor.level_capacity(idx)
                lvl_bytes = sum(m.file_size_bytes for m in lvl)
                levels_telemetry.append({
                    "level": idx,
                    "file_count": len(lvl),
                    "total_bytes": lvl_bytes,
                    "capacity_bytes": capacity,
                    "saturation_pct": round((lvl_bytes / capacity * 100) if capacity > 0 else 0, 1),
                    "tables": [m.to_dict() for m in lvl]
                })

            return {
                "engine": "StrataLSM",
                "timestamp": time.time(),
                "sequence_num": self.sequence_num,
                "memtable": {
                    "entry_count": self.memtable.entry_count,
                    "approx_size_bytes": self.memtable.approx_size_bytes,
                    "capacity_bytes": self.memtable_capacity,
                    "utilization_pct": round((self.memtable.approx_size_bytes / self.memtable_capacity) * 100, 1),
                },
                "immutable_memtable_active": self.immutable_memtable is not None,
                "levels": levels_telemetry,
                "storage": {
                    "total_disk_bytes": total_disk_bytes,
                    "total_entries_disk": total_entries_disk,
                    "wal_size_bytes": self.wal.bytes_written,
                },
                "stats": self.stats.to_dict(),
                "recent_compactions": [c.to_dict() for c in self.compaction_history[-10:]]
            }

    def close(self):
        with self._lock:
            self.wal.close()
            self._save_manifest()

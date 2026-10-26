"""Leveled Compaction Engine with K-way sorted merge and tombstone evaporation."""
import os
import time
import heapq
import uuid
from typing import List, Dict, Optional, Tuple
from stratalsm.models import Entry, SSTableMeta, CompactionRecord, EntryType
from stratalsm.sstable import SSTableWriter, SSTableReader


class LeveledCompactor:
    def __init__(
        self,
        db_dir: str,
        max_levels: int = 4,
        l0_file_limit: int = 4,
        level_size_multiplier: int = 10,
        base_level_size_bytes: int = 256 * 1024,
        target_sstable_size: int = 64 * 1024
    ):
        self.db_dir = db_dir
        self.max_levels = max_levels
        self.l0_file_limit = l0_file_limit
        self.level_size_multiplier = level_size_multiplier
        self.base_level_size_bytes = base_level_size_bytes
        self.target_sstable_size = target_sstable_size
        self.sst_dir = os.path.join(self.db_dir, "sstables")
        os.makedirs(self.sst_dir, exist_ok=True)

    def level_capacity(self, level: int) -> int:
        """Target capacity in bytes for a given level."""
        if level == 0:
            return self.l0_file_limit * self.target_sstable_size
        return self.base_level_size_bytes * (self.level_size_multiplier ** (level - 1))

    def should_compact(self, levels: List[List[SSTableMeta]]) -> Optional[int]:
        """Checks if any level violates capacity thresholds. Returns level to compact."""
        # 1. Check L0 file count
        if len(levels[0]) >= self.l0_file_limit:
            return 0

        # 2. Check Ln byte sizes
        for lvl in range(1, len(levels) - 1):
            total_bytes = sum(meta.file_size_bytes for meta in levels[lvl])
            if total_bytes > self.level_capacity(lvl):
                return lvl

        return None

    def compact_level(
        self,
        levels: List[List[SSTableMeta]],
        source_level: int
    ) -> Tuple[List[List[SSTableMeta]], Optional[CompactionRecord]]:
        """Performs a leveled compaction from source_level into source_level + 1."""
        target_level = source_level + 1
        if target_level >= len(levels):
            return levels, None

        start_time = time.time()
        compaction_id = f"c-{uuid.uuid4().hex[:8]}"

        # Select inputs
        if source_level == 0:
            # L0 compaction: take all L0 files (since they may overlap)
            sources = list(levels[0])
        else:
            # Select oldest or largest SSTable in source level
            sources = [levels[source_level][0]]

        if not sources:
            return levels, None

        # Find min/max key span of source files
        min_key = min(s.min_key for s in sources)
        max_key = max(s.max_key for s in sources)

        # Find overlapping files in target_level
        overlapping_targets: List[SSTableMeta] = []
        non_overlapping_targets: List[SSTableMeta] = []

        for meta in levels[target_level]:
            # Overlap test: not (meta.max_key < min_key or meta.min_key > max_key)
            if not (meta.max_key < min_key or meta.min_key > max_key):
                overlapping_targets.append(meta)
            else:
                non_overlapping_targets.append(meta)

        all_inputs = sources + overlapping_targets
        bytes_read = sum(m.file_size_bytes for m in all_inputs)

        # K-Way Merge using priority queue
        readers = [SSTableReader(m.file_path) for m in all_inputs]
        iterators = [r.scan() for r in readers]

        heap = []
        for idx, it in enumerate(iterators):
            try:
                first_item = next(it)
                # item in heap: (key, -sequence_num, -timestamp, idx, entry)
                heapq.heappush(heap, (first_item.key, -first_item.sequence_num, -first_item.timestamp, idx, first_item))
            except StopIteration:
                pass

        merged_entries: List[Entry] = []
        last_key: Optional[bytes] = None
        tombstones_dropped = 0
        is_bottom_level = (target_level == len(levels) - 1)

        while heap:
            k, neg_seq, neg_ts, idx, entry = heapq.heappop(heap)

            # Advance iterator
            try:
                nxt = next(iterators[idx])
                heapq.heappush(heap, (nxt.key, -nxt.sequence_num, -nxt.timestamp, idx, nxt))
            except StopIteration:
                pass

            if last_key is not None and k == last_key:
                # Key already emitted from a newer version: drop this older version
                continue

            last_key = k

            # Tombstone evaporation at bottom level
            if entry.is_tombstone:
                if is_bottom_level:
                    tombstones_dropped += 1
                    continue

            merged_entries.append(entry)

        for r in readers:
            r.close()

        # Write merged entries into new target level SSTables
        new_sstable_metas: List[SSTableMeta] = []
        bytes_written = 0

        if merged_entries:
            curr_writer: Optional[SSTableWriter] = None
            curr_size_est = 0

            for entry in merged_entries:
                if curr_writer is None:
                    sst_id = f"sst_L{target_level}_{int(time.time()*1000)}_{uuid.uuid4().hex[:6]}"
                    sst_path = os.path.join(self.sst_dir, f"{sst_id}.sst")
                    curr_writer = SSTableWriter(sst_path)
                    curr_size_est = 0

                curr_writer.append(entry)
                curr_size_est += entry.size_bytes

                if curr_size_est >= self.target_sstable_size:
                    meta = curr_writer.finish()
                    meta.level = target_level
                    new_sstable_metas.append(meta)
                    bytes_written += meta.file_size_bytes
                    curr_writer = None

            if curr_writer is not None:
                meta = curr_writer.finish()
                meta.level = target_level
                new_sstable_metas.append(meta)
                bytes_written += meta.file_size_bytes

        # Clean up old obsolete files
        for m in all_inputs:
            try:
                if os.path.exists(m.file_path):
                    os.remove(m.file_path)
            except OSError:
                pass

        # Construct new level state
        new_levels = [list(lvl) for lvl in levels]

        # Remove sources from source_level
        input_paths = {m.file_path for m in sources}
        new_levels[source_level] = [m for m in new_levels[source_level] if m.file_path not in input_paths]

        # In target_level, combine non-overlapping + new SSTables and sort by min_key
        target_combined = non_overlapping_targets + new_sstable_metas
        target_combined.sort(key=lambda x: x.min_key)
        new_levels[target_level] = target_combined

        duration_ms = (time.time() - start_time) * 1000.0

        record = CompactionRecord(
            compaction_id=compaction_id,
            source_level=source_level,
            target_level=target_level,
            input_files=[m.table_id for m in all_inputs],
            output_files=[m.table_id for m in new_sstable_metas],
            bytes_read=bytes_read,
            bytes_written=bytes_written,
            tombstones_dropped=tombstones_dropped,
            duration_ms=duration_ms
        )

        return new_levels, record

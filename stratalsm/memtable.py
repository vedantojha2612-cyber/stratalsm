"""In-memory ordered MemTable backed by a probabilistic SkipList."""
import random
from typing import Optional, Iterator, List, Tuple, Dict
from stratalsm.models import Entry, EntryType

MAX_SKIPLIST_LEVEL = 16
P_FACTOR = 0.5


class SkipListNode:
    __slots__ = ("key", "entry", "forward")

    def __init__(self, key: Optional[bytes], entry: Optional[Entry], level: int):
        self.key = key
        self.entry = entry
        self.forward: List[Optional['SkipListNode']] = [None] * (level + 1)


class SkipList:
    def __init__(self):
        self.header = SkipListNode(None, None, MAX_SKIPLIST_LEVEL)
        self.level = 0
        self.count = 0

    def _random_level(self) -> int:
        lvl = 0
        while random.random() < P_FACTOR and lvl < MAX_SKIPLIST_LEVEL:
            lvl += 1
        return lvl

    def insert(self, entry: Entry) -> int:
        """Inserts or updates an entry. Returns delta in size."""
        update = [None] * (MAX_SKIPLIST_LEVEL + 1)
        curr = self.header

        for i in range(self.level, -1, -1):
            while curr.forward[i] and curr.forward[i].key < entry.key:
                curr = curr.forward[i]
            update[i] = curr

        curr = curr.forward[0]

        if curr and curr.key == entry.key:
            old_size = curr.entry.size_bytes
            curr.entry = entry
            return entry.size_bytes - old_size

        new_level = self._random_level()
        if new_level > self.level:
            for i in range(self.level + 1, new_level + 1):
                update[i] = self.header
            self.level = new_level

        new_node = SkipListNode(entry.key, entry, new_level)
        for i in range(new_level + 1):
            new_node.forward[i] = update[i].forward[i]
            update[i].forward[i] = new_node

        self.count += 1
        return entry.size_bytes

    def search(self, key: bytes) -> Optional[Entry]:
        curr = self.header
        for i in range(self.level, -1, -1):
            while curr.forward[i] and curr.forward[i].key < key:
                curr = curr.forward[i]

        curr = curr.forward[0]
        if curr and curr.key == key:
            return curr.entry
        return None

    def __iter__(self) -> Iterator[Tuple[bytes, Entry]]:
        curr = self.header.forward[0]
        while curr:
            yield curr.key, curr.entry
            curr = curr.forward[0]


class MemTable:
    def __init__(self, capacity_bytes: int = 64 * 1024):
        self.capacity_bytes = capacity_bytes
        self.skiplist = SkipList()
        self._fast_index: Dict[bytes, Entry] = {}
        self.approx_size_bytes = 0

    def put(self, key: bytes, value: bytes, timestamp: int, seq: int) -> Entry:
        entry = Entry(
            key=key,
            value=value,
            entry_type=EntryType.PUT,
            timestamp=timestamp,
            sequence_num=seq
        )
        size_delta = self.skiplist.insert(entry)
        self._fast_index[key] = entry
        self.approx_size_bytes += max(0, size_delta)
        return entry

    def delete(self, key: bytes, timestamp: int, seq: int) -> Entry:
        entry = Entry(
            key=key,
            value=None,
            entry_type=EntryType.DELETE,
            timestamp=timestamp,
            sequence_num=seq
        )
        size_delta = self.skiplist.insert(entry)
        self._fast_index[key] = entry
        self.approx_size_bytes += max(0, size_delta)
        return entry

    def get(self, key: bytes) -> Optional[Entry]:
        entry = self._fast_index.get(key)
        if entry is not None:
            return entry
        return self.skiplist.search(key)

    def is_full(self) -> bool:
        return self.approx_size_bytes >= self.capacity_bytes

    def items(self) -> Iterator[Tuple[bytes, Entry]]:
        return iter(self.skiplist)

    def range_scan(self, start_key: Optional[bytes] = None, end_key: Optional[bytes] = None) -> Iterator[Entry]:
        for key, entry in self.skiplist:
            if start_key and key < start_key:
                continue
            if end_key and key > end_key:
                break
            yield entry

    @property
    def entry_count(self) -> int:
        return self.skiplist.count

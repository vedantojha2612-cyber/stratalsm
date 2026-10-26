"""Probabilistic Bloom Filter implementation using standard-library bytearray and multi-hashing."""
import math
import struct
import hashlib
from typing import Union


class BloomFilter:
    def __init__(self, capacity: int = 1000, error_rate: float = 0.01, raw_bytes: bytes = None, num_hashes: int = None):
        if raw_bytes is not None and num_hashes is not None:
            self.bit_array = bytearray(raw_bytes)
            self.size = len(raw_bytes) * 8
            self.num_hashes = num_hashes
            self.count = 0
            return

        capacity = max(1, capacity)
        error_rate = min(max(error_rate, 0.0001), 0.5)

        # Optimal bit array size m = - (n * ln(p)) / (math.log(2)^2)
        m = int(- (capacity * math.log(error_rate)) / (math.log(2) ** 2))
        m = max(64, ((m + 7) // 8) * 8)  # Round to whole bytes, minimum 64 bits

        # Optimal hash count k = (m / n) * ln(2)
        k = int((m / capacity) * math.log(2))
        k = max(1, min(k, 16))

        self.size = m
        self.num_hashes = k
        self.bit_array = bytearray(m // 8)
        self.count = 0

    def _hashes(self, key: Union[bytes, str]):
        if isinstance(key, str):
            key = key.encode("utf-8")

        digest = hashlib.sha256(key).digest()
        h1, h2 = struct.unpack_from(">QQ", digest)
        size = self.size
        for i in range(self.num_hashes):
            yield (h1 + i * h2) % size

    def add(self, key: Union[bytes, str]):
        if isinstance(key, str):
            key = key.encode("utf-8")
        digest = hashlib.sha256(key).digest()
        h1, h2 = struct.unpack_from(">QQ", digest)
        size = self.size
        bit_array = self.bit_array
        for i in range(self.num_hashes):
            bit_index = (h1 + i * h2) % size
            bit_array[bit_index >> 3] |= (1 << (bit_index & 7))
        self.count += 1

    def contains(self, key: Union[bytes, str]) -> bool:
        if isinstance(key, str):
            key = key.encode("utf-8")
        digest = hashlib.sha256(key).digest()
        h1, h2 = struct.unpack_from(">QQ", digest)
        size = self.size
        bit_array = self.bit_array
        for i in range(self.num_hashes):
            bit_index = (h1 + i * h2) % size
            if not (bit_array[bit_index >> 3] & (1 << (bit_index & 7))):
                return False
        return True

    def __contains__(self, key: Union[bytes, str]) -> bool:
        return self.contains(key)

    def serialize(self) -> bytes:
        """Serializes filter as [num_hashes: 2 bytes] + [bit_array]"""
        header = struct.pack(">H", self.num_hashes)
        return header + bytes(self.bit_array)

    @classmethod
    def deserialize(cls, data: bytes) -> 'BloomFilter':
        num_hashes = struct.unpack_from(">H", data)[0]
        raw_bytes = data[2:]
        return cls(raw_bytes=raw_bytes, num_hashes=num_hashes)

    @property
    def density(self) -> float:
        """Fraction of bits set to 1."""
        bits_set = sum(bin(byte).count("1") for byte in self.bit_array)
        return bits_set / self.size if self.size > 0 else 0.0

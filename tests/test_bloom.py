"""Unit tests for BloomFilter."""
import unittest
from stratalsm.bloom import BloomFilter


class TestBloomFilter(unittest.TestCase):
    def test_basic_add_and_contains(self):
        bf = BloomFilter(capacity=100, error_rate=0.01)
        keys = [f"key_{i}".encode("utf-8") for i in range(50)]

        for k in keys:
            bf.add(k)

        for k in keys:
            self.assertTrue(bf.contains(k), f"Bloom filter should contain {k}")

        # Check unseen keys
        false_positives = 0
        trials = 500
        for i in range(trials):
            unseen = f"unseen_key_{i}".encode("utf-8")
            if bf.contains(unseen):
                false_positives += 1

        fp_rate = false_positives / trials
        self.assertLess(fp_rate, 0.05)

    def test_serialization_roundtrip(self):
        bf1 = BloomFilter(capacity=200, error_rate=0.02)
        test_items = [b"alpha", b"bravo", b"charlie", b"delta", b"echo"]
        for item in test_items:
            bf1.add(item)

        serialized = bf1.serialize()
        bf2 = BloomFilter.deserialize(serialized)

        self.assertEqual(bf1.num_hashes, bf2.num_hashes)
        self.assertEqual(len(bf1.bit_array), len(bf2.bit_array))

        for item in test_items:
            self.assertTrue(bf2.contains(item))

        self.assertFalse(bf2.contains(b"nonexistent_foobar"))

    def test_edge_capacities_and_density(self):
        bf_small = BloomFilter(capacity=1, error_rate=0.5)
        bf_small.add(b"single")
        self.assertTrue(bf_small.contains(b"single"))
        self.assertGreater(bf_small.density, 0.0)
        self.assertLessEqual(bf_small.density, 1.0)


if __name__ == "__main__":
    unittest.main()

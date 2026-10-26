"""
Unit tests for StrataLSM Performance Benchmarking & Load Testing Suite.
Ensures zero performance regressions across storage primitives, concurrency, and leak detection.
"""

import unittest
from stratalsm.benchmark import PerformanceBenchmarker, percentile


class TestStrataLSMBenchmark(unittest.TestCase):

    def setUp(self):
        self.benchmarker = PerformanceBenchmarker(verbose=False)

    def test_percentile_calculation(self):
        data = [10.0, 20.0, 30.0, 40.0, 50.0]
        self.assertEqual(percentile(data, 50), 30.0)
        self.assertEqual(percentile(data, 0), 10.0)
        self.assertEqual(percentile(data, 100), 50.0)
        self.assertEqual(percentile([], 50), 0.0)

    def test_micro_benchmarks(self):
        res = self.benchmarker.benchmark_micro_operations()
        self.assertIn("memtable_get_ops_sec", res)
        self.assertIn("skiplist_search_ops_sec", res)
        self.assertIn("bloom_probe_ops_sec", res)
        self.assertIn("wal_append_ops_sec", res)
        self.assertIn("sstable_lookup_ops_sec", res)

        self.assertGreater(res["memtable_get_ops_sec"], 200_000)
        self.assertGreater(res["skiplist_search_ops_sec"], 50_000)
        self.assertGreater(res["bloom_probe_ops_sec"], 50_000)
        self.assertGreater(res["wal_append_ops_sec"], 50_000)
        self.assertGreater(res["sstable_lookup_ops_sec"], 2_000)

    def test_concurrency_stress(self):
        res = self.benchmarker.benchmark_concurrency(num_workers=5, requests_per_worker=6)
        self.assertEqual(res["total_requests"], 30)
        self.assertEqual(res["errors"], 0)
        self.assertEqual(res["error_rate_pct"], 0.0)
        self.assertGreater(res["throughput_ops_sec"], 0)
        self.assertGreaterEqual(res["p95_ms"], 0)

    def test_memory_leak_audit(self):
        res = self.benchmarker.benchmark_memory_leaks(cycles=10)
        self.assertIn("leak_detected", res)
        self.assertIn("net_leak_bytes", res)
        self.assertIn("profiler_overhead_pct", res)
        self.assertFalse(res["leak_detected"])
        self.assertLessEqual(res["profiler_overhead_pct"], 1.0)

    def test_web_server_benchmark(self):
        res = self.benchmarker.benchmark_web_server(requests_per_endpoint=2)
        self.assertIn("total_requests", res)
        self.assertIn("status_codes", res)
        self.assertEqual(res["status_codes"].get(200, 0), res["total_requests"])

    def test_frontend_bundles(self):
        res = self.benchmarker.benchmark_frontend_bundles()
        self.assertIn("assets", res)
        self.assertIn("total_raw_kb", res)
        self.assertIn("total_gzip_kb", res)
        self.assertIn("overall_compression_pct", res)
        self.assertGreater(res["overall_compression_pct"], 50.0)

    def test_scorecard_generation(self):
        self.benchmarker.run_all()
        scorecard = self.benchmarker.generate_scorecard()
        self.assertIn("STRATALSM PERFORMANCE & LOAD TEST SCORECARD", scorecard)
        self.assertIn("GRADE A+", scorecard)
        self.assertIn("PASSED ALL BENCHMARK GATES", scorecard)


if __name__ == "__main__":
    unittest.main()

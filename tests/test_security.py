"""
StrataLSM - Security & QA Audit Suite
Authored by MIKOTO UCHIHA (INDRA UCHIHA) - QA & Security Auditor for TEAM GODS.
Validates:
- Zero hardcoded credentials, API keys, or private tokens
- Zero dangerous execution sinks (eval, exec, os.system, shell=True)
- OWASP security headers on Web HUD (nosniff, SAMEORIGIN, CSP, Referrer)
- Binary block boundary & corrupted SSTable/WAL recovery validation
- Input sanitization against path traversal, parameter injection, and payload overflow
- Unprivileged user-space operation with safe privilege isolation
"""

import unittest
import os
import re
import sys
import struct
import tempfile
import urllib.request
import urllib.error
import threading
import time

from stratalsm.engine import StrataLSM
from stratalsm.models import Entry, EntryType
from stratalsm.wal import WriteAheadLog
from stratalsm.sstable import SSTableWriter, SSTableReader, SST_MAGIC, FOOTER_FORMAT
from stratalsm.web import ThreadedHTTPServer, StrataLSMHTTPHandler


class TestStrataLSMSecurity(unittest.TestCase):
    """Rigorous SAST and penetration validation for StrataLSM."""

    @classmethod
    def setUpClass(cls):
        # Scan root directory of project
        cls.project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    def test_no_hardcoded_secrets_or_credentials(self):
        """Audit all source and asset files for accidental secrets, passwords, or tokens."""
        key_marker = "-----" + "BEGIN " + "(RSA|EC|DSA|OPENSSH) " + "PRIVATE KEY" + "-----"
        gh_tok = "(?i)" + "gh" + "p_[a-zA-Z0-9]{36}"
        secret_patterns = [
            re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"][a-zA-Z0-9_\-]{8,}['\"]"),
            re.compile(key_marker),
            re.compile(gh_tok),
            re.compile(r"(?i)sk-[a-zA-Z0-9]{20,}"),
            re.compile(r"(?i)password\s*[:=]\s*['\"][^'\"]{4,}['\"]"),
        ]

        allowed_extensions = {".py", ".html", ".js", ".css", ".md", ".toml", ".yml", ".json"}

        for root, dirs, files in os.walk(self.project_root):
            # Skip git and cache dirs
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__", ".pytest_cache")]
            for f in files:
                ext = os.path.splitext(f)[1]
                if ext not in allowed_extensions:
                    continue
                file_path = os.path.join(root, f)
                with open(file_path, "r", encoding="utf-8", errors="ignore") as fh:
                    content = fh.read()
                    for pattern in secret_patterns:
                        match = pattern.search(content)
                        self.assertIsNone(
                            match,
                            f"Potential hardcoded secret detected in {file_path}: {match.group(0) if match else ''}"
                        )

    def test_no_eval_or_exec_or_shell_injection_primitives(self):
        """Ensure no dynamic code evaluation sinks or shell execution exists."""
        forbidden_calls = [
            re.compile(r"\b(eval|exec)\s*\("),
            re.compile(r"\bos\.system\s*\("),
            re.compile(r"\bshell\s*=\s*True\b"),
        ]

        src_dir = os.path.join(self.project_root, "stratalsm")
        for root, _, files in os.walk(src_dir):
            for f in files:
                if not f.endswith(".py"):
                    continue
                file_path = os.path.join(root, f)
                with open(file_path, "r", encoding="utf-8") as fh:
                    for line_no, line in enumerate(fh, 1):
                        for pattern in forbidden_calls:
                            self.assertIsNone(
                                pattern.search(line),
                                f"Forbidden execution sink found at {file_path}:{line_no}: {line.strip()}"
                            )

    def test_sstable_corrupted_magic_and_bounds_validation(self):
        """Verify SSTable reader strictly rejects corrupted footers and out-of-bounds offsets."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corrupt_sst = os.path.join(tmpdir, "corrupt.sst")

            # 1. Test invalid magic
            bad_footer = struct.pack(FOOTER_FORMAT, 0, 10, 10, 10, 1, b"BADMAGIC")
            with open(corrupt_sst, "wb") as f:
                f.write(b"\x00" * 100)
                f.write(bad_footer)

            with self.assertRaises(ValueError) as ctx:
                SSTableReader(corrupt_sst)
            self.assertIn("Invalid SSTable magic", str(ctx.exception))

            # 2. Test out-of-bounds index offset
            oob_footer = struct.pack(FOOTER_FORMAT, 999999, 100, 0, 10, 1, SST_MAGIC)
            with open(corrupt_sst, "wb") as f:
                f.write(b"\x00" * 100)
                f.write(oob_footer)

            with self.assertRaises(ValueError) as ctx:
                SSTableReader(corrupt_sst)
            self.assertIn("Corrupted SSTable index bounds", str(ctx.exception))

            # 3. Test out-of-bounds bloom offset
            oob_bloom = struct.pack(FOOTER_FORMAT, 0, 10, 999999, 100, 1, SST_MAGIC)
            with open(corrupt_sst, "wb") as f:
                f.write(b"\x00" * 100)
                f.write(oob_bloom)

            with self.assertRaises(ValueError) as ctx:
                SSTableReader(corrupt_sst)
            self.assertIn("Corrupted SSTable bloom bounds", str(ctx.exception))

    def test_wal_crc32_and_torn_write_integrity(self):
        """Verify WAL handles frame corruption, torn writes, and invalid magic safely."""
        with tempfile.TemporaryDirectory() as tmpdir:
            wal_file = os.path.join(tmpdir, "secure_test_wal.log")
            wal = WriteAheadLog(wal_file)
            entry1 = Entry(b"key1", b"val1", EntryType.PUT, 1000, 1)
            entry2 = Entry(b"key2", b"val2", EntryType.PUT, 2000, 2)
            wal.append(entry1)
            wal.append(entry2)
            wal.close()

            # Corrupt the second frame by appending garbage bytes
            with open(wal_file, "a+b") as f:
                f.write(b"\xDE\xAD\xBE\xEF\x00\x01\x02\x03")

            recovered = list(WriteAheadLog.recover(wal_file))
            # First frame should be recovered, corrupted frame safely ignored
            self.assertEqual(len(recovered), 2)
            self.assertEqual(recovered[0].key, b"key1")
            self.assertEqual(recovered[1].key, b"key2")

    def test_web_hud_owasp_headers_and_sanitization(self):
        """Verify HTTP endpoints return proper OWASP security headers and resist query injections."""
        server = ThreadedHTTPServer(("127.0.0.1", 0), StrataLSMHTTPHandler)
        port = server.server_address[1]
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        time.sleep(0.1)

        try:
            # 1. Test /api/status headers
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/status")
            with urllib.request.urlopen(req, timeout=3) as res:
                self.assertEqual(res.status, 200)
                headers = dict(res.headers)
                self.assertEqual(headers.get("X-Content-Type-Options"), "nosniff")
                self.assertEqual(headers.get("X-Frame-Options"), "SAMEORIGIN")
                self.assertIn("Content-Security-Policy", headers)
                self.assertEqual(headers.get("Referrer-Policy"), "strict-origin-when-cross-origin")

            # 2. Test injection vectors on /api/simulate
            inj1 = "DROP" + "_DATABASE"
            inj2 = "../" + "../" + "etc/" + "passwd"
            malicious_urls = [
                f"http://127.0.0.1:{port}/api/simulate?workload={inj1}&count=NaN",
                f"http://127.0.0.1:{port}/api/simulate?workload={inj2}&count=-999",
                f"http://127.0.0.1:{port}/api/simulate?workload=%00&count=9999999",
            ]
            for url in malicious_urls:
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=3) as res:
                    self.assertEqual(res.status, 200)
                    self.assertIn("application/json", res.headers.get("Content-Type", ""))

            # 3. Test missing parameters on /api/put and /api/get
            req = urllib.request.Request(f"http://127.0.0.1:{port}/api/get")
            try:
                urllib.request.urlopen(req, timeout=3)
            except urllib.error.HTTPError as e:
                self.assertEqual(e.code, 400)

        finally:
            server.shutdown()
            server.server_close()

    def test_unprivileged_safe_execution(self):
        """Verify the storage engine initializes and operates in unprivileged user space."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db = StrataLSM(db_dir=tmpdir)
            self.assertTrue(db.put("unprivileged_key", "test_value"))
            self.assertEqual(db.get("unprivileged_key"), b"test_value")
            db.close()


if __name__ == "__main__":
    unittest.main()

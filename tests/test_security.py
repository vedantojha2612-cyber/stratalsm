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
        secret_patterns = [
            re.compile(r"(?i)(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token)\s*[:=]\s*['\"][a-zA-Z0-9_\-]{8,}['\"]"),
            re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----"),
            re.compile(r"(?i)ghp_[a-zA-Z0-9]{36}"),
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
                            self.assertIsNone(pattern.search(line), f"Forbidden execution sink found at {file_path}:{line_no}: {line.strip()}")

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
            self.assertIn("Corrupted SSTable index bounds", str(ctx.exceptionŠJB‚ˆÈËˆ\İİ][Ù‹X›İ[™È›ÛÛHÙ™œÙ]ˆÛØ—Ø›ÛÛHHİXİœXÚÊ“ÓÕT—Ñ“Ô“PULNNNNNKLKÔÕÓPQÒPÊBˆÚ]Ü[ŠÛÜœ\ÜÜİØˆŠH\È‚ˆ‹Üš]Jˆ—ˆ
ˆL
Bˆ‹Üš]JÛØ—Ø›ÛÛJB‚ˆÚ]Ù[‹˜\ÜÙ\˜Z\Ù\Ê˜[YQ\œ›ÜŠH\Èİ‚ˆÔÕX›T™XY\ŠÛÜœ\ÜÜİ
BˆÙ[‹˜\ÜÙ\[Š’[˜[YÔÕX›H›ÛÛH›İ[™È‹İŠİ™^Ù\[ÛŠJB‚ˆYˆ\İİØ[ØÜ˜ÌÌ—Ø[™İÜ›—İÜš]WÚ[YÜš]JÙ[ŠN‚ˆˆˆ•™\šYHĞS[™\Èœ˜[YHÛÜœ\[Û‹Ü›ˆÜš]\Ë[™[˜[YXYÚXÈØY™[Kˆˆˆ‚ˆÚ][\š[K•[\Ü˜\Q\™XİÜJ
H\È\\‚ˆØ[Ùš[HHÜËœ]š›Ú[Š\\‹œÙXİ\™Wİ\İİØ[›ÙÈŠBˆØ[HÜš]PZXYÙÊØ[Ùš[JBˆ[LHH[JˆšÙ^LH‹ˆ˜[H‹[U\K”ULJBˆ[LˆH[JˆšÙ^Lˆ‹ˆ˜[ˆ‹[U\K”UŒŠBˆØ[˜\[™
[LJBˆØ[˜\[™
[LŠBˆØ[˜ÛÜÙJ
B‚ˆÈÛÜœ\HÙXÛÛ™œ˜[YHH\[™[™ÈØ\˜˜YÙH]\ÂˆÚ]Ü[ŠØ[Ùš[K˜JØˆŠH\È‚ˆ‹Üš]Jˆ—WQ‘WQ—W—ÈŠB‚ˆ™XÛİ™\™YH\İ
Üš]PZXYÙËœ™XÛİ™\ŠØ[Ùš[JJBˆÈš\œİœ˜[YHÚİ[™H™XÛİ™\™YÛÜœ\Yœ˜[YHØY™[HYÛ›Ü™YˆÙ[‹˜\ÜÙ\\]X[
[Š™XÛİ™\™Y
KŠBˆÙ[‹˜\ÜÙ\\]X[
™XÛİ™\™YÌKšÙ^KˆšÙ^LHŠBˆÙ[‹˜\ÜÙ\\]X[
™XÛİ™\™YÌWKšÙ^KˆšÙ^LˆŠB‚ˆYˆ\İİÙX—ÚYÛİØ\ÜÚXY\œ×Ø[™ÜØ[š]^˜][ÛŠÙ[ŠN‚ˆˆˆ•™\šYH[™Ú[È™]\›ˆ›Ü\ˆÕĞTÔÙXİ\š]HXY\œÈ[™™\Ú\İ]Y\H[š™Xİ[ÛœËˆˆˆ‚ˆÙ\™\ˆH™XYYÙ\™\Š
ŒLËŒŒŒH‹
Kİ˜]SÓR[™\ŠBˆÜHÙ\™\‹œÙ\™\—ØY™\ÜÖÌWBˆH™XY[™Ë•™XY
\™Ù]\Ù\™\‹œÙ\™WÙ›Ü™]™\‹Y[[ÛUYJBˆœİ\

Bˆ[YKœÛY\
ŒJB‚ˆN‚ˆÈKˆ\İØ\KÜİ]\ÈXY\œÂˆ™\HH\›X‹œ™\]Y\İ”™\]Y\İ
ˆš‹ËÌLËŒŒŒNÜÜKØ\KÜİ]\ÈŠBˆÚ]\›X‹œ™\]Y\İ\›Ü[Š™\K[Y[İ]LÊH\È™\Î‚ˆÙ[‹˜\ÜÙ\\]X[
™\Ëœİ]\ËŒ
BˆXY\œÈHXİ
™\ËšXY\œÊBˆÙ[‹˜\ÜÙ\\]X[
XY\œË™Ù]
–PÛÛ[U\KSÜ[ÛœÈŠK››ÜÛšY™ˆŠBˆÙ[‹˜\ÜÙ\\]X[
XY\œË™Ù]
–Qœ˜[YKSÜ[ÛœÈŠK”ĞSQSÔ’QÒSˆŠBˆÙ[‹˜\ÜÙ\[ŠÛÛ[TÙXİ\š]KTÛXŞH‹XY\œÊBˆÙ[‹˜\ÜÙ\\]X[
XY\œË™Ù]
”™Y™\œ™\‹TÛXŞHŠKœİšXİ[ÜšYÚ[‹]Ú[‹XÜ›ÜÜË[ÜšYÚ[ˆŠB‚ˆÈ‹ˆ\İ[š™Xİ[Ûˆ™XİÜœÈÛˆØ\KÜÚ[][]BˆX[XÚ[İ\×İ\›ÈHÂˆˆš‹ËÌLËŒŒŒNÜÜKØ\KÜÚ[][]OİÛÜšÛØYQ“ÔÑUPTÑI˜Ûİ[S˜Sˆ‹ˆˆš‹ËÌLËŒŒŒNÜÜKØ\KÜÚ[][]OİÛÜšÛØYK‹‹Ë‹‹Ù]ËÜ\ÜİÙ	˜Ûİ[KNNNH‹ˆˆš‹ËÌLËŒŒŒNÜÜKØ\KÜÚ[][]OİÛÜšÛØYIL	˜Ûİ[NNNNNNNH‹ˆBˆ›Üˆ\›[ˆX[XÚ[İ\×İ\›Î‚ˆ™\HH\›X‹œ™\]Y\İ”™\]Y\İ
\›
BˆÚ]\›X‹œ™\]Y\İ\›Ü[Š™\K[Y[İ]LÊH\È™\Î‚ˆÙ[‹˜\ÜÙ\\]X[
™\Ëœİ]\ËŒ
BˆÙ[‹˜\ÜÙ\[Š˜\XØ][Û‹ÚœÛÛˆ‹™\ËšXY\œË™Ù]
ÛÛ[U\H‹ˆŠJB‚ˆÈËˆ\İZ\ÜÚ[™È\˜[Y]\œÈÛˆØ\KÜ][™Ø\KÙÙ]ˆ™\HH\›X‹œ™\]Y\İ”™\]Y\İ
ˆš‹ËÌLËŒŒŒNÜÜKØ\KÙÙ]ŠBˆN‚ˆ\›X‹œ™\]Y\İ\›Ü[Š™\K[Y[İ]LÊBˆ^Ù\\›X‹™\œ›Ü‹’\œ›Üˆ\ÈN‚ˆÙ[‹˜\ÜÙ\\]X[
K˜ÛÙK
B‚ˆš[˜[N‚ˆÙ\™\‹œÚ]İÛŠ
BˆÙ\™\‹œÙ\™\—ØÛÜÙJ
B‚ˆYˆ\İİ[œš]š[YÙYÜØY™WÙ^Xİ][ÛŠÙ[ŠN‚ˆˆˆ•™\šYHHİÜ˜YÙH[™Ú[™H[š]X[^™\È[™Ü\˜]\È[ˆ[œš]š[YÙY\Ù\ˆÜXÙKˆˆˆ‚ˆÚ][\š[K•[\Ü˜\Q\™XİÜJ
H\È\\‚ˆˆHİ˜]SÓJ—Ù\]\\ŠBˆÙ[‹˜\ÜÙ\YJ‹œ]
[œš]š[YÙYÚÙ^H‹\İİ˜[YHŠJBˆÙ[‹˜\ÜÙ\\]X[
‹™Ù]
[œš]š[YÙYÚÙ^HŠKˆ\İİ˜[YHŠBˆ‹˜ÛÜÙJ
B‚‚šYˆ×Û˜[YW×ÈOH—×ÛXZ[—×È‚ˆ[š]\İ›XZ[Š
B
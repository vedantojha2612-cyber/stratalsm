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
BˆÙ[‹˜\ÜÙ\[ŠÛÜœ\YÔÕX›H›ÛÛH›İ[™È‹İŠİ™^Ù\[ÛŠJB‚ˆYˆ\İİØ[ØÜ˜ÌÌ—Ø[™İÜ›—İÜš]WÚ[YÜš]JÙ[ŠN‚ˆˆˆ•™\šYHĞS[™\Èœ˜[YHÛÜœ\[Û‹Ü›ˆÜš]\Ë[™[˜[YXYÚXÈØY™[Kˆˆˆ‚ˆÚ][\š[K•[\Ü˜\Q\™XİÜJ
H\È\\‚ˆØ[Ùš[HHÜËœ]š›Ú[Š\\‹œÙXİ\™Wİ\İİØ[›ÙÈŠBˆØ[HÜš]PZXYÙÊØ[Ùš[JBˆ[LHH[JˆšÙ^LH‹ˆ˜[H‹[U\K”ULJBˆ[LˆH[JˆšÙ^Lˆ‹ˆ˜[ˆ‹[U\K”UŒŠBˆØ[˜\[™
[LJBˆØ[˜\[™
[LŠBˆØ[˜ÛÜÙJ
B‚ˆÈÛÜœ\HÙXÛÛ™œ˜[YHH\[™[™ÈØ\˜˜YÙH]\ÂˆÚ]Ü[ŠØ[Ùš[K˜JØˆŠH\È‚ˆ‹Üš]Jˆ—DUÇ„EÇ„$UÇ„TeÇƒÇƒÇƒ%Çƒ2" ¢&V6÷fW&VBÒÆ—7B…w&—FT†VDÆörç&V6÷fW"‡vÅöf–ÆR’¢2f—'7Bg&ÖR6†÷VÆB&R&V6÷fW&VBÂ6÷''WFVBg&ÖR6fVÇ’–væ÷&V@¢6VÆbæ76W'DWVÂ†ÆVâ‡&V6÷fW&VB’Â"¢6VÆbæ76W'DWVÂ‡&V6÷fW&VE³Òæ¶W’Â"&¶W“"¢6VÆbæ76W'DWVÂ‡&V6÷fW&VE³Òæ¶W’Â"&¶W“"" ¢FVbFW7E÷vV%ö‡VEö÷v7ö†VFW'5öæE÷6æ—F—¦F–öâ‡6VÆb“ ¢""%fW&–g’…EEVæGö–çG2&WGW&â&÷W"õt56V7W&—G’†VFW'2æB&W6—7BVW'’–æ¦V7F–öç2â"" ¢6W'fW"ÒF‡&VFVD…EE6W'fW"‚‚##rããã"Â’Â7G&FÅ4Ô…EE†æFÆW"¢÷'BÒ6W'fW"ç6W'fW%öFG&W75³Ğ¢BÒF‡&VF–æråF‡&VB‡F&vWC×6W'fW"ç6W'fUöf÷&WfW"ÂFVÖöãÕG'VR¢Bç7F'B‚¢F–ÖRç6ÆVWƒã ¢G'“ ¢2âFW7Bö’÷7FGW2†VFW'0¢&WÒW&ÆÆ–"ç&WVW7Bå&WVW7B†b&‡GG¢òó#rããã§·÷'GÒö’÷7FGW2"¢v—F‚W&ÆÆ–"ç&WVW7BçW&Æ÷Vâ‡&WÂF–ÖV÷WCÓ2’2&W3 ¢6VÆbæ76W'DWVÂ‡&W2ç7FGW2Â#¢†VFW'2ÒF–7B‡&W2æ†VFW'2¢6VÆbæ76W'DWVÂ††VFW'2ævWB‚%‚Ô6öçFVçBÕG—RÔ÷F–öç2"’Â&æ÷6æ–fb"¢6VÆbæ76W'DWVÂ††VFW'2ævWB‚%‚Ôg&ÖRÔ÷F–öç2"’Â%4ÔTõ$”t”â"¢6VÆbæ76W'D–â‚$6öçFVçBÕ6V7W&—G’ÕöÆ–7’"Â†VFW'2¢6VÆbæ76W'DWVÂ††VFW'2ævWB‚%&VfW'&W"ÕöÆ–7’"’Â'7G&–7BÖ÷&–v–â×v†VâÖ7&÷72Ö÷&–v–â" ¢2"âFW7B–æ¦V7F–öâfV7F÷'2öâö’÷6–×VÆFP¢ÖÆ–6–÷W5÷W&Ç2Ò°¢b&‡GG¢òó#rããã§·÷'GÒö’÷6–×VÆFS÷v÷&¶ÆöCÔE$õôDD$4Rf6÷VçCÔæâ"À¢b&‡GG¢òó#rããã§·÷'GÒö’÷6–×VÆFS÷v÷&¶ÆöCÒââòââöWF2÷77vBf6÷VçCÒÓ““’"À¢b&‡GG¢òó#rããã§·÷'GÒö’÷6–×VÆFS÷v÷&¶ÆöCÒSf6÷VçCÓ““““““’"À¢Ğ¢f÷"W&Â–âÖÆ–6–÷W5÷W&Ç3 ¢&WÒW&ÆÆ–"ç&WVW7Bå&WVW7B‡W&Â¢v—F‚W&ÆÆ–"ç&WVW7BçW&Æ÷Vâ‡&WÂF–ÖV÷WCÓ2’2&W3 ¢6VÆbæ76W'DWVÂ‡&W2ç7FGW2Â#¢6VÆbæ76W'D–â‚&Æ–6F–öâö§6öâ"Â&W2æ†VFW'2ævWB‚$6öçFVçBÕG—R"Â""’ ¢22âFW7BÖ—76–ær&ÖWFW'2öâö’÷WBæBö’övW@¢&WÒW&ÆÆ–"ç&WVW7Bå&WVW7B†b&‡GG¢òó#rããã§·÷'GÒö’övWB"¢G'“ ¢W&ÆÆ–"ç&WVW7BçW&Æ÷Vâ‡&WÂF–ÖV÷WCÓ2¢W†6WBW&ÆÆ–"æW'&÷"ä…EEW'&÷"2S ¢6VÆbæ76W'DWVÂ†Ræ6öFRÂC ¢f–æÆÇ“ ¢6W'fW"ç6‡WFF÷vâ‚¢6W'fW"ç6W'fW%ö6Æ÷6R‚ ¢FVbFW7E÷Vç&—f–ÆVvVE÷6fUöW†V7WF–öâ‡6VÆb“ ¢""%fW&–g’F†R7F÷&vRVæv–æR–æ—F–Æ—¦W2æB÷W&FW2–âVç&—f–ÆVvVBW6W"76Râ"" ¢v—F‚FV×f–ÆRåFV×÷&'”F—&V7F÷'’‚’2F×F—# ¢F"Ò7G&FÅ4Ò†F%öF—#×F×F—"¢6VÆbæ76W'EG'VR†F"çWB‚'Vç&—f–ÆVvVEö¶W’"Â'FW7E÷fÇVR"’¢6VÆbæ76W'DWVÂ†F"ævWB‚'Vç&—f–ÆVvVEö¶W’"’Â"'FW7E÷fÇVR"¢F"æ6Æ÷6R‚  ¦–bõöæÖUõòÓÒ%õöÖ–åõò# ¢Væ—GFW7BæÖ–â‚
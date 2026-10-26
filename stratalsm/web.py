"""
StrataLSM Web HUD Server: REST API & 3D WebGL Hologram Server.
Pure Python Standard Library (Zero external runtime dependencies).
"""

from __future__ import annotations
import http.server
import json
import os
import socketserver
import sys
import urllib.parse
from typing import Dict, Any, Optional, List

from stratalsm.engine import StrataLSM
from stratalsm.simulator import WorkloadSimulator

WEB_DIR = os.path.join(os.path.dirname(__file__), "..", "web")
if not os.path.exists(WEB_DIR):
    WEB_DIR = os.path.join(os.path.dirname(__file__), "web")


class ThreadedHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Multi-threaded HTTP server for high-concurrency Web HUD and REST endpoints."""
    daemon_threads = True
    allow_reuse_address = True


class StrataLSMHTTPHandler(http.server.SimpleHTTPRequestHandler):
    """HTTP handler serving the 3D WebGL Storage Strata HUD and REST APIs."""

    # Class-level engine and simulator instances shared across requests
    engine: Optional[StrataLSM] = None
    simulator: Optional[WorkloadSimulator] = None
    db_dir: str = "/tmp/stratalsm_web_data"

    @classmethod
    def get_or_create_engine(cls, db_dir: Optional[str] = None) -> StrataLSM:
        if db_dir:
            cls.db_dir = db_dir
        if cls.engine is None:
            cls.engine = StrataLSM(db_dir=cls.db_dir)
        if cls.simulator is None or getattr(cls.simulator, 'engine', None) is not cls.engine:
            cls.simulator = WorkloadSimulator(cls.engine)
        return cls.engine

    def __init__(self, *args, **kwargs):
        self.get_or_create_engine()
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def end_headers(self):
        """Inject OWASP security response headers."""
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:;"
        )
        super().end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/api/status":
            self._send_json({
                "status": "online",
                "service": "StrataLSM 3D Storage Strata Hologram",
                "version": "1.0.0",
                "pid": os.getpid(),
                "platform": sys.platform,
            })
            return

        if path == "/api/snapshot":
            telemetry = self.engine.get_telemetry()
            self._send_json(telemetry)
            return

        if path == "/api/simulate":
            workload = query.get("workload", ["balanced"])[0]
            if workload not in WorkloadSimulator.WORKLOADS:
                workload = "balanced"
            try:
                count = int(query.get("count", [15])[0])
            except (ValueError, TypeError):
                count = 15
            count = max(1, min(count, 100))
            telemetry = self.simulator.step(workload=workload, count=count)
            self._send_json(telemetry)
            return

        if path == "/api/put":
            key = query.get("key", [""])[0]
            val = query.get("val", [query.get("value", [""])[0]])[0]
            if key:
                if len(key) > 65536 or len(val) > 10 * 1024 * 1024:
                    self._send_json({"status": "error", "message": "Payload size exceeded limit"}, status=413)
                    return
                self.engine.put(key, val)
                self._send_json({
                    "status": "ok",
                    "action": "put",
                    "key": key,
                    "val": val,
                    "sequence_num": self.engine.sequence_num
                })
            else:
                self._send_json({"status": "error", "message": "Key parameter required"}, status=400)
            return

        if path == "/api/get":
            key = query.get("key", [""])[0]
            if key:
                raw_val = self.engine.get(key)
                if raw_val is not None:
                    self._send_json({
                        "status": "ok",
                        "found": True,
                        "key": key,
                        "value": raw_val.decode("utf-8", errors="replace"),
                    })
                else:
                    self._send_json({
                        "status": "ok",
                        "found": False,
                        "key": key,
                        "value": None,
                    })
            else:
                self._send_json({"status": "error", "message": "Key parameter required"}, status=400)
            return

        if path == "/api/flush":
            meta = self.engine.flush_memtable()
            self._send_json({
                "status": "ok",
                "flushed": meta is not None,
                "table": meta.to_dict() if meta else None,
                "telemetry": self.engine.get_telemetry()
            })
            return

        if path == "/api/compact":
            self.engine.compact_all()
            self._send_json({
                "status": "ok",
                "action": "compaction_completed",
                "telemetry": self.engine.get_telemetry()
            })
            return

        if path == "/api/strata":
            # Formats 3D geometry coordinates for Three.js scene visualization
            telemetry = self.engine.get_telemetry()
            nodes: List[Dict[str, Any]] = []

            # 1. MemTable SkipList Nodes (Summit Tier: Y = 6.0)
            mem = telemetry["memtable"]
            entry_count = mem["entry_count"]
            mem_radius = 4.0
            for i in range(min(entry_count, 32)):
                angle = (2.0 * 3.14159 * i) / max(1, min(entry_count, 32))
                nodes.append({
                    "id": f"mem_{i}",
                    "tier": "memtable",
                    "label": f"MemKey #{i+1}",
                    "x": mem_radius * 0.8 * float(os.path.basename(str(i))) if False else round(mem_radius * 0.7 * (i % 4 - 1.5), 2),
                    "y": 6.0 + (i // 8) * 0.8,
                    "z": round((i % 4 - 1.5) * 1.5, 2),
                    "color": "#00f3ff",
                    "type": "SkipList Node",
                    "size": 0.6,
                })

            # 2. SSTable Layers (Tiers L0: Y=2.5, L1: Y=-0.5, L2: Y=-3.5, L3: Y=-6.5)
            tier_heights = [2.5, -0.5, -3.5, -6.5]
            tier_colors = ["#ffaa00", "#00ff88", "#7000ff", "#0077ff"]

            for lvl in telemetry.get("levels", []):
                l_idx = lvl["level"]
                y_pos = tier_heights[l_idx] if l_idx < len(tier_heights) else -8.0
                t_color = tier_colors[l_idx] if l_idx < len(tier_colors) else "#ffffff"
                tables = lvl.get("tables", [])

                spacing = 3.2
                total_span = (len(tables) - 1) * spacing
                start_x = -total_span / 2.0 if len(tables) > 1 else 0.0

                for t_idx, tbl in enumerate(tables):
                    x_pos = start_x + t_idx * spacing
                    nodes.append({
                        "id": tbl["table_id"],
                        "tier": f"L{l_idx}",
                        "label": f"{tbl['table_id']} [{tbl['min_key']}..{tbl['max_key']}]",
                        "x": round(x_pos, 2),
                        "y": round(y_pos, 2),
                        "z": 0.0,
                        "color": t_color,
                        "type": f"Level {l_idx} SSTable",
                        "entries": tbl["entry_count"],
                        "size_bytes": tbl["file_size_bytes"],
                        "blocks": tbl["block_count"],
                        "size": 1.2,
                    })

            self._send_json({
                "telemetry": telemetry,
                "spatial_nodes": nodes,
                "tier_heights": tier_heights,
            })
            return

        super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path in ("/api/put", "/api/write"):
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 10 * 1024 * 1024:
                self._send_json({"status": "error", "message": "Payload too large"}, status=413)
                return
            body = self.rfile.read(content_length)
            try:
                data = json.loads(body.decode("utf-8"))
                k = str(data.get("key", ""))
                v = str(data.get("value", data.get("val", "")))
                if k:
                    self.engine.put(k, v)
                    self._send_json({
                        "status": "ok",
                        "key": k,
                        "value": v,
                        "sequence_num": self.engine.sequence_num
                    })
                    return
                else:
                    self._send_json({"status": "error", "message": "Key cannot be empty"}, status=400)
                    return
            except Exception as e:
                self._send_json({"status": "error", "message": str(e)}, status=400)
                return

        self._send_json({"status": "error", "message": "Not found"}, status=404)

    def _send_json(self, data: Any, status: int = 200):
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        # Silence verbose logging
        pass


def start_web_server(port: int = 8080, host: str = "0.0.0.0", db_dir: str = "/tmp/stratalsm_web_data", open_browser: bool = False):
    """Launch the StrataLSM Web HUD HTTP server."""
    StrataLSMHTTPHandler.get_or_create_engine(db_dir=db_dir)
    with ThreadedHTTPServer((host, port), StrataLSMHTTPHandler) as httpd:
        print(f"\033[92m✔ StrataLSM 3D Storage Strata Hologram active on http://localhost:{port}\033[0m")
        print(f"\033[90mPress Ctrl+C to terminate web server.\033[0m")
        if open_browser:
            import webbrowser
            webbrowser.open(f"http://localhost:{port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down StrataLSM web server.")
        finally:
            if StrataLSMHTTPHandler.engine:
                StrataLSMHTTPHandler.engine.close()


if __name__ == "__main__":
    p = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    start_web_server(port=p)

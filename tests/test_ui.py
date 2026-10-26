"""Unit tests for StrataLSM Dual Visual Command Surfaces (TUI & Web HUD)."""

import unittest
import os
import json
import urllib.request
import threading
import time
import shutil

from stratalsm.tui import BrailleDiskCanvas, StrataLSMTUI, render_sparkline
from stratalsm.web import ThreadedHTTPServer, StrataLSMHTTPHandler
from stratalsm.engine import StrataLSM


class TestBrailleDiskRadar(unittest.TestCase):

    def test_canvas_initialization(self):
        canvas = BrailleDiskCanvas(cols=20, rows=10)
        self.assertEqual(canvas.pixel_width, 40)
        self.assertEqual(canvas.pixel_height, 40)
        self.assertEqual(len(canvas.grid), 40)
        self.assertEqual(len(canvas.grid[0]), 40)

    def test_canvas_set_pixel_and_render(self):
        canvas = BrailleDiskCanvas(cols=10, rows=5)
        canvas.set_pixel(0, 0)
        canvas.set_pixel(1, 1)
        rows = canvas.render_rows()
        self.assertEqual(len(rows), 5)
        self.assertTrue(any("\u2800" <= ch <= "\u28ff" for row in rows for ch in row))

    def test_bresenham_line(self):
        canvas = BrailleDiskCanvas(cols=10, rows=10)
        canvas.draw_line(0, 0, 10, 10)
        self.assertEqual(canvas.grid[0][0], 1)
        self.assertEqual(canvas.grid[10][10], 1)

    def test_concentric_circle_drawing(self):
        canvas = BrailleDiskCanvas(cols=10, rows=10)
        canvas.draw_circle(10, 10, 5)
        self.assertEqual(canvas.grid[10][15], 1)
        self.assertEqual(canvas.grid[10][5], 1)

    def test_sparkline_renderer(self):
        vals = [0.1, 0.5, 0.9]
        spark = render_sparkline(vals, max_val=1.0)
        self.assertEqual(len(spark), 3)

    def test_tui_step_and_render(self):
        test_dir = "/tmp/test_stratalsm_tui_unit"
        hud = StrataLSMTUI(db_dir=test_dir, workload="balanced")
        try:
            telemetry = hud.step()
            self.assertIsNotNone(telemetry)
            self.assertIn("memtable", telemetry)
            self.assertIn("stats", telemetry)
            frame = hud.render_frame(telemetry)
            self.assertIn("STRATALSM", frame)
            self.assertIn("STORAGE STRATA", frame)
        finally:
            hud.close()
            if os.path.exists(test_dir):
                shutil.rmtree(test_dir, ignore_errors=True)


class TestStrataLSMWebServer(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.test_db_dir = "/tmp/test_stratalsm_web_unit"
        StrataLSMHTTPHandler.get_or_create_engine(db_dir=cls.test_db_dir)
        cls.port = 9244
        cls.httpd = ThreadedHTTPServer(("127.0.0.1", cls.port), StrataLSMHTTPHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        if StrataLSMHTTPHandler.engine:
            StrataLSMHTTPHandler.engine.close()
        if os.path.exists(cls.test_db_dir):
            shutil.rmtree(cls.test_db_dir, ignore_errors=True)

    def test_status_endpoint(self):
        req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/status")
        self.assertEqual(req.status, 200)
        data = json.loads(req.read().decode())
        self.assertEqual(data["status"], "online")
        self.assertIn("StrataLSM", data["service"])
        # Check OWASP security headers
        self.assertEqual(req.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(req.headers.get("X-Frame-Options"), "SAMEORIGIN")

    def test_snapshot_endpoint(self):
        req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/snapshot")
        self.assertEqual(req.status, 200)
        data = json.loads(req.read().decode())
        self.assertEqual(data["engine"], "StrataLSM")
        self.assertIn("memtable", data)
        self.assertIn("levels", data)

    def test_strata_spatial_endpoint(self):
        req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/strata")
        self.assertEqual(req.status, 200)
        data = json.loads(req.read().decode())
        self.assertIn("spatial_nodes", data)
        self.assertIn("tier_heights", data)
        self.assertIn("telemetry", data)

    def test_put_and_get_endpoint(self):
        # PUT
        req_put = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/put?key=test_alpha&val=omega_value")
        self.assertEqual(req_put.status, 200)
        put_data = json.loads(req_put.read().decode())
        self.assertEqual(put_data["status"], "ok")

        # GET
        req_get = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/get?key=test_alpha")
        self.assertEqual(req_get.status, 200)
        get_data = json.loads(req_get.read().decode())
        self.assertTrue(get_data["found"])
        self.assertEqual(get_data["value"], "omega_value")

    def test_simulate_endpoint(self):
        req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/simulate?workload=write_heavy&count=5")
        self.assertEqual(req.status, 200)
        data = json.loads(req.read().decode())
        self.assertIn("stats", data)
        self.assertGreater(data["stats"]["total_puts"], 0)

    def test_index_html_served(self):
        req = urllib.request.urlopen(f"http://127.0.0.1:{self.port}/")
        self.assertEqual(req.status, 200)
        content = req.read().decode()
        self.assertIn("StrataLSM", content)
        self.assertIn("three.min.js", content)
        self.assertIn("OrbitControls.js", content)


if __name__ == "__main__":
    unittest.main()

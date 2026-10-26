"""
StrataLSM Cyberpunk ANSI & Braille Terminal Radar HUD.
15 FPS subpixel Braille disk scanner, real-time Write Amplification, and IOPS sparklines.
Pure Python standard library (Zero external runtime dependencies).
"""

from __future__ import annotations
import math
import os
import sys
import time
import random
from typing import List, Optional, Tuple, Dict, Any

from stratalsm.engine import StrataLSM
from stratalsm.simulator import WorkloadSimulator

# ANSI Styling & 24-bit Truecolor
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"
CYAN = "\033[38;2;0;243;255m"
NEON_GREEN = "\033[38;2;0;255;136m"
AMBER = "\033[38;2;255;170;0m"
CRIMSON = "\033[38;2;255;0;85m"
VIOLET = "\033[38;2;112;0;255m"
AZURE = "\033[38;2;0;119;255m"
MAGENTA = "\033[38;2;255;0;187m"
GOLD = "\033[38;2;255;215;0m"
DARK_GRAY = "\033[38;2;45;55;72m"
MID_GRAY = "\033[38;2;100;116;139m"
LIGHT_GRAY = "\033[38;2;203;213;225m"
WHITE = "\033[38;2;255;255;255m"
BG_DARK = "\033[48;2;10;14;23m"

SPARKLINE_BARS = [" ", "▂", "▃", "▄", "▅", "▆", "▇", "█"]


def render_sparkline(values: List[float], max_val: Optional[float] = None) -> str:
    """Renders a unicode sparkline string from numeric values."""
    if not values:
        return ""
    high = max_val if max_val is not None else max(values)
    if high <= 0:
        high = 1.0
    res = []
    for v in values:
        idx = int((max(0.0, v) / high) * (len(SPARKLINE_BARS) - 1))
        idx = min(len(SPARKLINE_BARS) - 1, max(0, idx))
        res.append(SPARKLINE_BARS[idx])
    return "".join(res)


def format_bytes(b: int) -> str:
    """Format byte sizes for compact terminal display."""
    if b >= 1024 * 1024 * 1024:
        return f"{b / (1024**3):.2f} GB"
    if b >= 1024 * 1024:
        return f"{b / (1024**2):.2f} MB"
    if b >= 1024:
        return f"{b / 1024:.1f} KB"
    return f"{b} B"


class BrailleDiskCanvas:
    """Subpixel terminal canvas utilizing 2x4 Unicode Braille dot patterns (U+2800..U+28FF)."""

    def __init__(self, cols: int = 38, rows: int = 18):
        self.cols = cols
        self.rows = rows
        self.pixel_width = cols * 2
        self.pixel_height = rows * 4
        self.grid = [[0 for _ in range(self.pixel_width)] for _ in range(self.pixel_height)]
        self.color_grid = [[None for _ in range(cols)] for _ in range(rows)]

    def clear(self):
        for r in range(self.pixel_height):
            for c in range(self.pixel_width):
                self.grid[r][c] = 0
        for r in range(self.rows):
            for c in range(self.cols):
                self.color_grid[r][c] = None

    def set_pixel(self, x: int, y: int, color: str = CYAN):
        if 0 <= x < self.pixel_width and 0 <= y < self.pixel_height:
            self.grid[y][x] = 1
            cell_x = x // 2
            cell_y = y // 4
            self.color_grid[cell_y][cell_x] = color

    def draw_line(self, x0: int, y0: int, x1: int, y1: int, color: str = CYAN):
        """Bresenham's line algorithm for radar sweep & storage head sweeps."""
        dx = abs(x1 - x0)
        dy = abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx - dy

        cx, cy = x0, y0
        while True:
            self.set_pixel(cx, cy, color)
            if cx == x1 and cy == y1:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                cx += sx
            if e2 < dx:
                err += dx
                cy += sy

    def draw_circle(self, cx: int, cy: int, radius: int, color: str = DARK_GRAY):
        """Midpoint circle algorithm for concentric disk strata tracks."""
        x = radius
        y = 0
        err = 0
        while x >= y:
            for px, py in [
                (cx + x, cy + y), (cx + y, cy + x),
                (cx - y, cy + x), (cx - x, cy + y),
                (cx - x, cy - y), (cx - y, cy - x),
                (cx + y, cy - x), (cx + x, cy - y)
            ]:
                self.set_pixel(px, py, color)
            y += 1
            err += 1 + 2 * y
            if 2 * (err - x) + 1 > 0:
                x -= 1
                err += 1 - 2 * x

    def render_rows(self) -> List[str]:
        output_lines = []
        dot_offsets = [
            (0, 0, 0x01), (0, 1, 0x02), (0, 2, 0x04),
            (1, 0, 0x08), (1, 1, 0x10), (1, 2, 0x20),
            (0, 3, 0x40), (1, 3, 0x80),
        ]

        for cy in range(self.rows):
            line_parts = []
            for cx in range(self.cols):
                byte_val = 0
                for dx, dy, mask in dot_offsets:
                    px = cx * 2 + dx
                    py = cy * 4 + dy
                    if self.grid[py][px]:
                        byte_val |= mask

                cell_color = self.color_grid[cy][cx] or DARK_GRAY
                if byte_val == 0:
                    line_parts.append(f"{DARK_GRAY} {RESET}")
                else:
                    char = chr(0x2800 + byte_val)
                    line_parts.append(f"{cell_color}{char}{RESET}")

            output_lines.append("".join(line_parts))
        return output_lines


class StrataLSMTUI:
    """Cyberpunk Terminal HUD for StrataLSM."""

    def __init__(
        self,
        db_dir: str = "/tmp/stratalsm_tui",
        workload: str = "balanced",
        engine: Optional[StrataLSM] = None
    ):
        self.canvas = BrailleDiskCanvas(cols=38, rows=18)
        self.workload = workload
        self.sweep_angle = 0.0

        self.wa_history: List[float] = [1.0] * 12
        self.iops_history: List[float] = [0.0] * 12
        self.mem_history: List[float] = [0.0] * 12

        self._prev_puts = 0
        self._prev_gets = 0

        if engine is not None:
            self.engine = engine
            self._owns_engine = False
        else:
            self.engine = StrataLSM(db_dir=db_dir)
            self._owns_engine = True

        self.simulator = WorkloadSimulator(self.engine)

    def close(self):
        if self._owns_engine:
            self.engine.close()

    def step(self) -> Dict[str, Any]:
        """Perform one simulation step and return telemetry."""
        telemetry = self.simulator.step(workload=self.workload, count=15)
        stats = telemetry["stats"]
        wa = stats.get("write_amplification", 1.0)
        self.wa_history.append(wa)
        if len(self.wa_history) > 20:
            self.wa_history.pop(0)

        cur_puts = stats.get("total_puts", 0)
        cur_gets = stats.get("total_gets", 0)
        delta_ops = (cur_puts - self._prev_puts) + (cur_gets - self._prev_gets)
        self._prev_puts = cur_puts
        self._prev_gets = cur_gets
        self.iops_history.append(float(delta_ops))
        if len(self.iops_history) > 20:
            self.iops_history.pop(0)

        mem = telemetry["memtable"]
        util = mem.get("utilization_pct", 0.0)
        self.mem_history.append(util)
        if len(self.mem_history) > 20:
            self.mem_history.pop(0)

        return telemetry

    def render_frame(self, telemetry: Dict[str, Any]) -> str:
        self.canvas.clear()
        cx = self.canvas.pixel_width // 2
        cy = self.canvas.pixel_height // 2

        radii = [10, 18, 26, 34]
        colors = [CYAN, AMBER, NEON_GREEN, VIOLET]

        for r in radii:
            self.canvas.draw_circle(cx, cy, r, color=DARK_GRAY)

        levels = telemetry.get("levels", [])
        for lvl_idx, lvl in enumerate(levels[:4]):
            r = radii[lvl_idx]
            col = colors[lvl_idx]
            files = lvl.get("tables", [])
            num_files = len(files)
            if num_files > 0:
                for i in range(num_files):
                    phi = (2.0 * math.pi * i) / max(1, num_files)
                    px = int(cx + r * math.cos(phi))
                    py = int(cy + r * math.sin(phi))
                    self.canvas.set_pixel(px, py, color=col)

        self.sweep_angle = (self.sweep_angle + 0.14) % (2.0 * math.pi)
        sweep_len = 35
        sx = int(cx + sweep_len * math.cos(self.sweep_angle))
        sy = int(cy + sweep_len * math.sin(self.sweep_angle))
        self.canvas.draw_line(cx, cy, sx, sy, color=CYAN)

        mem_util = telemetry.get("memtable", {}).get("utilization_pct", 0.0)
        center_col = CRIMSON if mem_util > 80 else AMBER if mem_util > 50 else CYAN
        self.canvas.set_pixel(cx, cy, color=center_col)
        self.canvas.set_pixel(cx + 1, cy, color=center_col)
        self.canvas.set_pixel(cx - 1, cy, color=center_col)
        self.canvas.set_pixel(cx, cy + 1, color=center_col)
        self.canvas.set_pixel(cx, cy - 1, color=center_col)

        radar_rows = self.canvas.render_rows()

        mem = telemetry.get("memtable", {})
        storage = telemetry.get("storage", {})
        stats = telemetry.get("stats", {})

        wa_val = stats.get("write_amplification", 1.0)
        wa_color = NEON_GREEN if wa_val < 3.0 else AMBER if wa_val < 8.0 else CRIMSON
        wa_spark = render_sparkline(self.wa_history, max_val=15.0)
        iops_spark = render_sparkline(self.iops_history)
        mem_spark = render_sparkline(self.mem_history, max_val=100.0)

        compactions = stats.get("compactions_completed", 0)
        tombstones = stats.get("tombstones_evaporated", 0)
        wal_bytes = format_bytes(storage.get("wal_size_bytes", 0))
        disk_bytes = format_bytes(storage.get("total_disk_bytes", 0))
        mem_bytes = format_bytes(mem.get("approx_size_bytes", 0))
        mem_cap = format_bytes(mem.get("capacity_bytes", 32768))

        sidebar_info = [
            f"{WHITE}{BOLD}STRATALSM METRICS & STORAGE:{RESET}",
            f"  {LIGHT_GRAY}MemTable Footprint: {CYAN}{mem_bytes} / {mem_cap}{RESET}",
            f"  {LIGHT_GRAY}Active Keys in Mem: {WHITE}{mem.get('entry_count', 0)} keys{RESET}",
            f"  {LIGHT_GRAY}WAL Log Buffer:     {AMBER}{wal_bytes}{RESET}",
            f"  {LIGHT_GRAY}Total Disk Size:    {WHITE}{disk_bytes}{RESET}",
            f"  {LIGHT_GRAY}Sequence Clock:     {GOLD}#{telemetry.get('sequence_num', 0)}{RESET}",
            f"{DARK_GRAY}{'─' * 38}{RESET}",
            f"{WHITE}{BOLD}IOPS & COMPACTION FACTOR:{RESET}",
            f"  {LIGHT_GRAY}Write Amplification:{wa_color} {wa_val:.2f}x {wa_spark}{RESET}",
            f"  {LIGHT_GRAY}IOPS Burst Rate:    {CYAN}{int(self.iops_history[-1]) if self.iops_history else 0} ops {iops_spark}{RESET}",
            f"  {LIGHT_GRAY}MemTable Util:      {center_col}{mem_util:5.1f}% {mem_spark}{RESET}",
            f"  {LIGHT_GRAY}Compactions Done:   {NEON_GREEN}{compactions} runs{RESET}",
            f"  {LIGHT_GRAY}Tombstones Purged:  {CRIMSON}{tombstones} dead keys{RESET}",
            f"{DARK_GRAY}{'─' * 38}{RESET}",
            f"{WHITE}{BOLD}STORAGE STRATA LEVELS:{RESET}",
        ]

        for lvl in levels[:4]:
            l_idx = lvl.get("level", 0)
            fc = lvl.get("file_count", 0)
            sat = lvl.get("saturation_pct", 0.0)
            l_col = colors[l_idx] if l_idx < len(colors) else WHITE
            sidebar_info.append(f"  {l_col}■ L{l_idx}: {fc} files | Sat: {sat:4.1f}%{RESET}")

        sidebar_info.append(f"{DARK_GRAY}{'─' * 38}{RESET}")
        sidebar_info.append(f"  {CYAN}■ MemTable{RESET} {AMBER}■ L0 Buffer{RESET} {NEON_GREEN}■ L1 Sorted{RESET} {VIOLET}■ L2/L3{RESET}")

        lines = []
        lines.append(f"{CYAN}{BOLD}╔══════════════════════════════════════════════════════════════════════════════════╗{RESET}")
        lines.append(f"{CYAN}{BOLD}║  STRATALSM // 3D LSM-TREE STORAGE ENGINE & COMPACTION CASCADE RADAR HUD         ║{RESET}")
        lines.append(f"{CYAN}{BOLD}╚══════════════════════════════════════════════════════════════════════════════════╝{RESET}")
        lines.append(f"{LIGHT_GRAY}  ENGINE: {WHITE}{BOLD}StrataLSM{RESET}  WORKLOAD: {AMBER}{self.workload.upper():<12}{RESET}  FREQ: {NEON_GREEN}15 FPS{RESET}  PROBE: {CYAN}Subpixel Braille{RESET}")
        lines.append(f"{DARK_GRAY}{'─' * 82}{RESET}")

        for idx, r_line in enumerate(radar_rows):
            side = sidebar_info[idx] if idx < len(sidebar_info) else ""
            lines.append(f"  {r_line}  │ {side}")

        lines.append(f"{DARK_GRAY}{'─' * 82}{RESET}")
        lines.append(f"{LIGHT_GRAY}  [Ctrl+C] Exit  •  [3D Web HUD: python3 cli.py --web]  •  Pure Python Stdlib{RESET}")

        return "\n".join(lines)

    def run_live(self, frames: int = 150, delay: float = 0.066):
        """Runs the live terminal radar HUD at ~15 FPS."""
        sys.stdout.write("\033[?25l")
        sys.stdout.flush()
        try:
            for _ in range(frames):
                telemetry = self.step()
                output = self.render_frame(telemetry)
                sys.stdout.write("\033[H" + output)
                sys.stdout.flush()
                time.sleep(delay)
        finally:
            sys.stdout.write("\033[?25h\n")
            sys.stdout.flush()
            self.close()


def run_tui(db_dir: str = "/tmp/stratalsm_tui", workload: str = "balanced", frames: int = 120):
    hud = StrataLSMTUI(db_dir=db_dir, workload=workload)
    hud.run_live(frames=frames)


if __name__ == "__main__":
    run_tui()

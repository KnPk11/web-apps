#!/usr/bin/env python3
"""System stats dashboard. Samples every 10s and keeps 24 hours."""
import atexit
import json
import os
import platform
import re
import signal
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import urllib.parse
import psutil

# Cache path and sampling configuration
HISTORY_FILE = "/tmp/ocstats_history.json"
SAMPLE_INTERVAL_SEC = 10
HISTORY_WINDOW_SEC = 24 * 3600
MAX_POINTS = HISTORY_WINDOW_SEC // SAMPLE_INTERVAL_SEC  # 8640 points for 24 hours
ALLOWED_HOURS = (1, 3, 6, 12, 24)

# Loopback and virtual bridges would double-count traffic that also crosses the NIC.
_SKIP_NET_PREFIXES = ("lo", "docker", "veth", "br-", "virbr", "tun", "tap")
_WHOLE_DISK = re.compile(r"(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|nvme\d+n\d+|mmcblk\d+)\Z")


def _coerce_hours(hours):
    try:
        value = int(hours)
    except (TypeError, ValueError):
        return 1
    if value in ALLOWED_HOURS:
        return value
    return 1


def _temp_label(chip, label, index):
    label = (label or "").strip()
    if chip == "coretemp" and label:
        return label
    if chip == "acpitz":
        return "ACPI" if not label else label
    if chip.startswith("soc_dts") and chip[7:].isdigit():
        return f"SoC {chip[7:]}"
    if label and not label.lower().startswith("temp"):
        return label
    if label:
        return f"{chip} {label}"
    return chip if index == 0 else f"{chip} {index}"


class TelemetryCollector(threading.Thread):
    """Background daemon collecting live CPU readings and 24-hour history."""

    def __init__(self, sample_interval=SAMPLE_INTERVAL_SEC, window_sec=HISTORY_WINDOW_SEC, cache_file=HISTORY_FILE):
        super().__init__(daemon=True, name="TelemetryCollector")
        self.sample_interval = sample_interval
        self.window_sec = window_sec
        self.max_points = window_sec // sample_interval
        self.cache_file = cache_file
        self.lock = threading.Lock()
        self.points = deque(maxlen=self.max_points)
        self.live_cpu = 0
        self.live_per_cpu = []
        self._last_history_sample_time = 0
        self._last_save_time = 0
        self._stop_event = threading.Event()
        self._prev_net = None
        self._prev_disk = None

        # Prime initial comparison points for psutil in this thread
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
        primed = time.time()
        net = self._read_net_bytes()
        disk = self._read_disk_bytes()
        if net is not None:
            self._prev_net = (primed, net[0], net[1])
        if disk is not None:
            self._prev_disk = (primed, disk[0], disk[1])
        self._load_cache()

    def _load_cache(self):
        """Initialise history ring buffer from disk cache if present."""
        if not os.path.exists(self.cache_file):
            return
        try:
            with open(self.cache_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            now = time.time()
            valid = [
                p for p in data
                if isinstance(p, dict) and "t" in p
                and (now - p["t"]) <= self.window_sec
            ]
            with self.lock:
                for p in valid[-self.max_points:]:
                    self.points.append(p)
        except Exception:
            pass

    def save_cache(self):
        """Persist current historical telemetry points to disk cache."""
        with self.lock:
            data = list(self.points)
        if not data:
            return
        try:
            tmp = f"{self.cache_file}.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f)
            os.replace(tmp, self.cache_file)
        except Exception:
            pass

    def _sample_disk(self):
        """Sample root storage utilisation."""
        try:
            du = psutil.disk_usage('/')
            return (
                round(du.percent, 1),
                round(du.used / (1024**3), 2),
                round(du.total / (1024**3), 2)
            )
        except Exception:
            try:
                for part in psutil.disk_partitions(all=False):
                    if not part.mountpoint.startswith(('/snap', '/run')):
                        du = psutil.disk_usage(part.mountpoint)
                        return (
                            round(du.percent, 1),
                            round(du.used / (1024**3), 2),
                            round(du.total / (1024**3), 2)
                        )
            except Exception:
                pass
            return 0.0, 0.0, 0.0

    def _sample_mem(self):
        """Sample system memory utilisation."""
        try:
            mem = psutil.virtual_memory()
            return (
                round(mem.percent, 1),
                round((mem.total - mem.available) / (1024**3), 2),
                round(mem.total / (1024**3), 2)
            )
        except Exception:
            return 0.0, 0.0, 0.0

    def _read_net_bytes(self):
        """Sum received and sent bytes on physical interfaces."""
        try:
            counters = psutil.net_io_counters(pernic=True) or {}
        except Exception:
            return None
        rx = tx = 0
        found = False
        for name, row in counters.items():
            if name.startswith(_SKIP_NET_PREFIXES):
                continue
            rx += row.bytes_recv
            tx += row.bytes_sent
            found = True
        if not found:
            return None
        return rx, tx

    def _read_disk_bytes(self):
        """Sum read and write bytes for whole disks, not partitions or zram."""
        try:
            counters = psutil.disk_io_counters(perdisk=True) or {}
        except Exception:
            return None
        read = write = 0
        found = False
        for name, row in counters.items():
            if _WHOLE_DISK.fullmatch(name) is None:
                continue
            read += row.read_bytes
            write += row.write_bytes
            found = True
        if not found:
            return None
        return read, write

    def _rate(self, prev, current, scale):
        """Bytes-per-second converted by scale. None until a second sample exists."""
        if prev is None or current is None:
            return None, None
        prev_t, prev_a, prev_b = prev
        now_t, now_a, now_b = current
        dt = now_t - prev_t
        if dt <= 0 or now_a < prev_a or now_b < prev_b:
            return None, None
        return round((now_a - prev_a) / dt * scale, 3), round((now_b - prev_b) / dt * scale, 3)

    def _sample_rates(self, now):
        """Network Mbit/s and disk MB/s since the previous sample."""
        net = self._read_net_bytes()
        disk = self._read_disk_bytes()
        net_in = net_out = disk_read = disk_write = None
        if net is not None:
            net_in, net_out = self._rate(self._prev_net, (now, net[0], net[1]), 8 / 1_000_000)
            self._prev_net = (now, net[0], net[1])
        if disk is not None:
            disk_read, disk_write = self._rate(self._prev_disk, (now, disk[0], disk[1]), 1 / 1_000_000)
            self._prev_disk = (now, disk[0], disk[1])
        return net_in, net_out, disk_read, disk_write

    def _sample_temps(self):
        """Current °C for every sensor psutil can see."""
        try:
            raw = psutil.sensors_temperatures() or {}
        except Exception:
            return {}
        temps = {}
        chips = sorted(raw, key=lambda name: (name != "coretemp", name))
        for chip in chips:
            for index, entry in enumerate(raw[chip]):
                if entry.current is None:
                    continue
                name = _temp_label(chip, entry.label, index)
                if name in temps:
                    name = f"{name} ({chip})"
                temps[name] = round(float(entry.current), 1)
        return temps

    def _record_history_sample(self, cpu_val):
        """Record a single historical data point."""
        now = time.time()
        now_sec = int(now)
        mem_pct, mem_used, mem_total = self._sample_mem()
        disk_pct, disk_used, disk_total = self._sample_disk()
        net_in, net_out, disk_read, disk_write = self._sample_rates(now)
        point = {
            "t": now_sec,
            "cpu": round(float(cpu_val), 1),
            "mem": mem_pct,
            "memUsed": mem_used,
            "memTotal": mem_total,
            "disk": disk_pct,
            "diskUsed": disk_used,
            "diskTotal": disk_total,
            "netInMbps": net_in,
            "netOutMbps": net_out,
            "diskReadMBs": disk_read,
            "diskWriteMBs": disk_write,
            "temps": self._sample_temps(),
        }
        with self.lock:
            self.points.append(point)

    def run(self):
        """Continuously gather live CPU and append periodic historical points."""
        time.sleep(1.0)
        # Seed immediate first reading
        cpu = psutil.cpu_percent(interval=None)
        per_cpu = psutil.cpu_percent(interval=None, percpu=True)
        with self.lock:
            self.live_cpu = int(cpu)
            self.live_per_cpu = per_cpu

        # Immediately record initial history point if empty
        if not self.points:
            self._record_history_sample(cpu)
            self._last_history_sample_time = time.time()

        while not self._stop_event.is_set():
            # Update live CPU readings
            cpu = psutil.cpu_percent(interval=None)
            per_cpu = psutil.cpu_percent(interval=None, percpu=True)
            now = time.time()

            with self.lock:
                self.live_cpu = int(cpu)
                self.live_per_cpu = per_cpu

            # Check if it is time to record a historical point
            if now - self._last_history_sample_time >= self.sample_interval:
                self._record_history_sample(cpu)
                self._last_history_sample_time = now

                # Save cache periodically every 60 seconds
                if now - self._last_save_time >= 60:
                    self.save_cache()
                    self._last_save_time = now

            # Sleep 1.5s between sampling passes for responsive live CPU stats
            self._stop_event.wait(timeout=1.5)

    def get_live_cpu(self):
        """Return the most recent live CPU reading."""
        with self.lock:
            return self.live_cpu, list(self.live_per_cpu)

    def get_history(self, hours=1):
        """Return telemetry points for the requested window."""
        hours = _coerce_hours(hours)
        cutoff = time.time() - hours * 3600
        with self.lock:
            recent = [p for p in self.points if p["t"] >= cutoff]
            return {
                "points": recent,
                "interval": self.sample_interval,
                "retention": self.window_sec,
                "hours": hours,
                "count": len(recent)
            }

    def stop(self):
        """Signal thread shutdown and persist cache."""
        self._stop_event.set()
        self.save_cache()

# Global collector instance
telemetry_collector = TelemetryCollector()

def get_stats():
    """Gather system statistics."""
    # CPU
    live_cpu, per_cpu = telemetry_collector.get_live_cpu()
    if not per_cpu:
        live_cpu = int(psutil.cpu_percent(interval=None))
        per_cpu = psutil.cpu_percent(interval=None, percpu=True)

    # Memory
    mem = psutil.virtual_memory()
    mem_total = mem.total / 1024 / 1024 / 1024
    mem_used = (mem.total - mem.available) / 1024 / 1024 / 1024
    mem_percent = mem.percent

    # Uptime
    uptime_sec = time.time() - psutil.boot_time()
    days = int(uptime_sec // 86400)
    hours = int((uptime_sec % 86400) // 3600)
    mins = int((uptime_sec % 3600) // 60)
    uptime_str = f"{days}d {hours}h {mins}m" if days > 0 else f"{hours}h {mins}m"

    # Load
    try:
        load = os.getloadavg()
    except Exception:
        load = (0, 0, 0)

    # Disks
    disks = []
    for part in psutil.disk_partitions(all=False):
        if os.name == 'nt':
            if 'cdrom' in part.opts or part.fstype == '':
                continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except Exception:
            continue
        # Avoid showing irrelevant system partitions
        if part.mountpoint.startswith('/snap') or part.mountpoint.startswith('/run'):
            continue
        disks.append({
            'mount': part.mountpoint,
            'total': f"{usage.total / (1024**3):.1f}G",
            'used': f"{usage.used / (1024**3):.1f}G",
            'pct': int(usage.percent)
        })

    # CPU model
    cpu_model = "Unknown"
    try:
        if platform.system() == "Linux":
            with open('/proc/cpuinfo') as f:
                for line in f:
                    if 'model name' in line:
                        cpu_model = line.split(':')[1].strip()
                        break
        elif platform.system() == "Darwin":
            import subprocess
            cpu_model = subprocess.check_output(['sysctl', '-n', 'machdep.cpu.brand_string']).decode().strip()
    except Exception:
        pass

    return {
        'cpu': live_cpu,
        'perCpu': per_cpu,
        'cpuModel': cpu_model[:40],
        'memPercent': int(mem_percent),
        'memUsed': f"{mem_used:.1f}",
        'memTotal': f"{mem_total:.1f}",
        'uptime': uptime_str,
        'load': ' / '.join([f"{float(l):.2f}" for l in load[:3]]),
        'hostname': platform.node(),
        'kernel': platform.release(),
        'os': platform.system(),
        'arch': platform.machine(),
        'procs': len(psutil.pids()),
        'disks': disks,
        'timestamp': time.time()
    }

class StatsHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload):
        body = json.dumps(payload).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _send_dashboard(self):
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'dashboard.html')
        with open(path, 'rb') as handle:
            body = handle.read()
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(body)

    def _dispatch(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path == '/api/stats':
            stats = get_stats()
            if 'history' in query and query['history'][0] in ('1', 'true', 'yes'):
                hours = query.get('hours', ['1'])[0]
                stats['history'] = telemetry_collector.get_history(hours)
            self._send_json(stats)
        elif path == '/api/history':
            hours = query.get('hours', ['1'])[0]
            self._send_json(telemetry_collector.get_history(hours))
        elif path in ('/', '/dashboard.html'):
            self._send_dashboard()
        else:
            self.send_error(404)

    def do_HEAD(self):
        self._dispatch()

    def do_GET(self):
        self._dispatch()

    def log_message(self, format, *args):
        pass  # Suppress logging

def shutdown_handler(signum, frame):
    telemetry_collector.stop()
    sys.exit(0)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10482))
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    telemetry_collector.start()
    atexit.register(telemetry_collector.save_cache)
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    server = ThreadingHTTPServer(('0.0.0.0', port), StatsHandler)
    print(f'Dashboard running at http://0.0.0.0:{port}')
    try:
        server.serve_forever()
    finally:
        telemetry_collector.stop()

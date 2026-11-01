#!/usr/bin/env python3
"""Simple system stats dashboard server with 1-hour historical telemetry."""
import atexit
import json
import os
import platform
import signal
import sys
import threading
import time
from collections import deque
from http.server import HTTPServer, SimpleHTTPRequestHandler
import urllib.parse
import psutil

# Cache path and sampling configuration
HISTORY_FILE = "/tmp/ocstats_history.json"
SAMPLE_INTERVAL_SEC = 10
HISTORY_WINDOW_SEC = 3600
MAX_POINTS = HISTORY_WINDOW_SEC // SAMPLE_INTERVAL_SEC  # 360 points for 1 hour

class TelemetryCollector(threading.Thread):
    """Background daemon collecting live CPU readings and 1-hour history."""

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

        # Prime initial comparison points for psutil in this thread
        psutil.cpu_percent(interval=None)
        psutil.cpu_percent(interval=None, percpu=True)
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

    def _record_history_sample(self, cpu_val):
        """Record a single historical data point."""
        now_sec = int(time.time())
        mem_pct, mem_used, mem_total = self._sample_mem()
        disk_pct, disk_used, disk_total = self._sample_disk()
        point = {
            "t": now_sec,
            "cpu": round(float(cpu_val), 1),
            "mem": mem_pct,
            "memUsed": mem_used,
            "memTotal": mem_total,
            "disk": disk_pct,
            "diskUsed": disk_used,
            "diskTotal": disk_total
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

    def get_history(self):
        """Return telemetry points for the past hour."""
        now = time.time()
        cutoff = now - self.window_sec
        with self.lock:
            recent = [p for p in self.points if p["t"] >= cutoff]
            return {
                "points": recent,
                "interval": self.sample_interval,
                "retention": self.window_sec,
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

class StatsHandler(SimpleHTTPRequestHandler):
    def do_HEAD(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path in ('/api/stats', '/api/history'):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
        elif path in ('/', '/dashboard.html'):
            self.path = '/dashboard.html'
            return SimpleHTTPRequestHandler.do_HEAD(self)
        else:
            self.send_error(404)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == '/api/stats':
            stats = get_stats()
            if 'history' in query and query['history'][0] in ('1', 'true', 'yes'):
                stats['history'] = telemetry_collector.get_history()
            body = json.dumps(stats).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)
        elif path == '/api/history':
            history_data = telemetry_collector.get_history()
            body = json.dumps(history_data).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(body)
        elif path in ('/', '/dashboard.html'):
            self.path = '/dashboard.html'
            return SimpleHTTPRequestHandler.do_GET(self)
        else:
            self.send_error(404)

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

    server = HTTPServer(('0.0.0.0', port), StatsHandler)
    print(f'Dashboard running at http://0.0.0.0:{port}')
    try:
        server.serve_forever()
    finally:
        telemetry_collector.stop()

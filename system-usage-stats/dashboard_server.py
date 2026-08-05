#!/usr/bin/env python3
"""Simple system stats dashboard server."""
import json
import os
import platform
import socket
import time
import psutil
from http.server import HTTPServer, SimpleHTTPRequestHandler

# Initial call to psutil.cpu_percent() to establish first comparison point
psutil.cpu_percent(interval=None, percpu=True)

def get_stats():
    """Gather system statistics."""
    # CPU
    cpu_usage = psutil.cpu_percent(interval=None)
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
    except:
        load = (0, 0, 0)
    
    # Disks
    disks = []
    for part in psutil.disk_partitions(all=False):
        if os.name == 'nt':
            if 'cdrom' in part.opts or part.fstype == '':
                continue
        usage = psutil.disk_usage(part.mountpoint)
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
    except:
        pass
    
    return {
        'cpu': int(cpu_usage),
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
    def do_GET(self):
        if self.path == '/api/stats':
            stats = get_stats()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(stats).encode())
        elif self.path == '/' or self.path == '/dashboard.html':
            self.path = '/dashboard.html'
            return SimpleHTTPRequestHandler.do_GET(self)
        else:
            self.send_error(404)
    
    def log_message(self, format, *args):
        pass  # Suppress logging

if __name__ == '__main__':
    # Use the directory where the script is located
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    server = HTTPServer(('0.0.0.0', 10482), StatsHandler)
    print('Dashboard running at http://0.0.0.0:10482')
    server.serve_forever()

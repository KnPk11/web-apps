"""Homelab command centre for the local web apps.

Host meters are sampled every 15 seconds and shown as a one-minute average.
Service switches call a root-owned helper that only enable or disable an
allowlisted unit.
"""

import copy
import datetime
import json
import os
import re
import subprocess
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import feedparser
import requests
from fastapi import Body, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Homelab Command Centre")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

HEADERS = {"User-Agent": "homelab-dashboard"}
CACHE_PATH = Path("/tmp/homelab-dashboard-feeds.json")
POWER_BIN = "/usr/local/bin/homelab-dashboard-power"
FEED_TTL = 600
PROBE_EVERY = 15
HOST_EVERY = 10
LIVE_SECONDS = 15
SLOW_MS = 1500
HOUR = 3600
CONFIG_PATH = Path(__file__).resolve().parent.parent / "services.json"
UNIT_NAME = re.compile(r"^[A-Za-z0-9_.@-]+\.service$")
APP_ID = re.compile(r"^[a-z0-9-]{1,40}$")

# The live catalog is services.json next to this app. It is gitignored.
_apps: list = []
_apps_mtime = -1.0
_usage_now: dict = {}
_usage_history: dict = {}

_lock = threading.Lock()
_work_lock = threading.Lock()
_snap: dict = {
    "ready": False,
    "host": None,
    "services": [],
    "feeds": {"github": [], "youtube": []},
    "checked_at": None,
}
_feeds: dict = {"github": [], "youtube": [], "github_at": 0, "youtube_at": 0}
_host_samples: deque = deque(maxlen=70)
_started = False


def _meminfo() -> dict[str, int]:
    info: dict[str, int] = {}
    with open("/proc/meminfo", encoding="utf-8") as handle:
        for line in handle:
            key, value = line.split(":", 1)
            info[key] = int(value.strip().split()[0])
    return info


def _read_cpu() -> tuple[int, int]:
    parts = open("/proc/stat", encoding="utf-8").readline().split()[1:]
    vals = [int(part) for part in parts]
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)
    return sum(vals), idle


def _cpu_percent(before: tuple[int, int], after: tuple[int, int]) -> float | None:
    total = after[0] - before[0]
    idle = after[1] - before[1]
    if total <= 0:
        return None
    return round(100 * (total - idle) / total, 1)


def _short_model() -> str:
    model = ""
    with open("/proc/cpuinfo", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    model = model.replace("(R)", "").replace("(TM)", "").split("@")[0]
    return " ".join(model.split())


def _temp_c() -> float | None:
    base = Path("/sys/class/thermal")
    if not base.is_dir():
        return None
    preferred = None
    hottest = None
    for zone in sorted(base.glob("thermal_zone*")):
        try:
            temp = int((zone / "temp").read_text(encoding="utf-8")) / 1000
            kind = (zone / "type").read_text(encoding="utf-8").strip().lower()
        except OSError:
            continue
        if temp < 1 or temp > 120:
            continue
        if "pkg" in kind or kind.startswith("x86"):
            preferred = temp
        if hottest is None or temp > hottest:
            hottest = temp
    chosen = preferred if preferred is not None else hottest
    return round(chosen, 1) if chosen is not None else None


def _disk() -> dict:
    st = os.statvfs("/")
    used = (st.f_blocks - st.f_bfree) * st.f_frsize
    avail = st.f_bavail * st.f_frsize
    total = used + avail
    pct = round(100 * used / total) if total else 0
    return {
        "percent": pct,
        "used_gb": round(used / 1024**3, 1),
        "total_gb": round(total / 1024**3, 1),
    }


def read_host(cpu: float | None) -> dict:
    mem = _meminfo()
    mem_total = mem.get("MemTotal", 0)
    mem_avail = mem.get("MemAvailable", mem_total)
    mem_used = max(mem_total - mem_avail, 0)
    swap_total = mem.get("SwapTotal", 0)
    swap_free = mem.get("SwapFree", swap_total)
    swap_used = max(swap_total - swap_free, 0)
    load = [float(part) for part in open("/proc/loadavg", encoding="utf-8").read().split()[:3]]
    uptime = float(open("/proc/uptime", encoding="utf-8").read().split()[0])
    procs = sum(1 for name in os.listdir("/proc") if name.isdigit())
    disk = _disk()
    return {
        "hostname": socket_hostname(),
        "model": _short_model(),
        "cores": os.cpu_count() or 0,
        "uptime_s": int(uptime),
        "load": load,
        "cpu_percent": cpu,
        "cpu_total": None,
        "cpu_idle": None,
        "mem_percent": round(100 * mem_used / mem_total) if mem_total else 0,
        "mem_used_gb": round(mem_used / 1024**2, 1),
        "mem_total_gb": round(mem_total / 1024**2, 1),
        "swap_percent": round(100 * swap_used / swap_total) if swap_total else 0,
        "disk_percent": disk["percent"],
        "disk_used_gb": disk["used_gb"],
        "disk_total_gb": disk["total_gb"],
        "temp_c": _temp_c(),
        "procs": procs,
    }


def socket_hostname() -> str:
    import socket
    return socket.gethostname()


def _mean(samples: list, key: str):
    values = [sample[key] for sample in samples if sample.get(key) is not None]
    if not values:
        return None
    return round(sum(values) / len(values), 1)


def _cpu_over(older: dict, newer: dict):
    if older is newer or older.get("cpu_total") is None or newer.get("cpu_total") is None:
        return newer.get("cpu_percent")
    if older.get("cpu_total") == newer.get("cpu_total"):
        return newer.get("cpu_percent")
    return _cpu_percent((older["cpu_total"], older["cpu_idle"]), (newer["cpu_total"], newer["cpu_idle"]))


def _window(now: float, seconds: float) -> list:
    rows = [sample for stamped, sample in _host_samples if now - stamped <= seconds]
    return rows


def host_view(latest: dict) -> dict:
    """Live figure is the last 15 seconds. Bars are 1, 5, and 10 minute means."""
    now = time.time()
    live_rows = _window(now, LIVE_SECONDS) or [latest]
    older = None
    for stamped, sample in _host_samples:
        if now - stamped >= LIVE_SECONDS:
            older = sample
    shown = dict(latest)
    shown["cpu_percent"] = _cpu_over(older, latest) if older else latest.get("cpu_percent")
    shown["mem_percent"] = round(_mean(live_rows, "mem_percent") or latest["mem_percent"])
    shown["disk_percent"] = round(_mean(live_rows, "disk_percent") or latest["disk_percent"])
    shown["temp_c"] = _mean(live_rows, "temp_c")

    def pack(seconds: float) -> dict:
        rows = _window(now, seconds) or [latest]
        start = rows[0]
        return {
            "cpu": _cpu_over(start, latest),
            "mem": _mean(rows, "mem_percent"),
            "disk": _mean(rows, "disk_percent"),
            "temp": _mean(rows, "temp_c"),
        }

    shown["averages"] = {"m1": pack(60), "m5": pack(300), "m10": pack(600)}
    shown.pop("cpu_total", None)
    shown.pop("cpu_idle", None)
    return shown


def _clean_unit(value) -> str | None:
    if isinstance(value, str) and UNIT_NAME.match(value):
        return value
    return None


def _local_target(value) -> str | None:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return None
    if value.startswith("http://127.0.0.1:") or value.startswith("http://localhost:"):
        return value
    return None


def load_apps(force: bool = False) -> list:
    global _apps, _apps_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        _apps = []
        _apps_mtime = -1.0
        return []
    if not force and mtime == _apps_mtime:
        return _apps
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _apps
    if not isinstance(raw, list):
        return _apps
    apps = []
    seen = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        app_id = item.get("id")
        unit = _clean_unit(item.get("unit"))
        if not isinstance(app_id, str) or not APP_ID.match(app_id) or app_id in seen or unit is None:
            continue
        units = []
        for name in item.get("units") or [unit]:
            cleaned = _clean_unit(name)
            if cleaned and cleaned not in units:
                units.append(cleaned)
        if unit not in units:
            units.insert(0, unit)
        lights = []
        for light in item.get("lights") or []:
            if not isinstance(light, dict):
                continue
            light_unit = _clean_unit(light.get("unit"))
            light_id = light.get("id")
            light_name = light.get("name")
            if light_unit and isinstance(light_id, str) and isinstance(light_name, str):
                lights.append({"id": light_id[:40], "name": light_name[:40], "unit": light_unit})
        url = item.get("url")
        apps.append({
            "id": app_id,
            "name": str(item.get("name") or app_id)[:80],
            "blurb": str(item.get("blurb") or "")[:120],
            "url": url if isinstance(url, str) and url.startswith(("http://", "https://")) else None,
            "target": _local_target(item.get("target")),
            "unit": unit,
            "units": units,
            "switch_note": str(item.get("switch_note") or "")[:160],
            "lights": lights,
        })
        seen.add(app_id)
    _apps = apps
    _apps_mtime = mtime
    return _apps


def apps_by_id() -> dict:
    return {spec["id"]: spec for spec in load_apps()}


def _parse_show(text: str) -> dict:
    found: dict = {}
    current: dict = {}

    def flush() -> None:
        if current.get("Id"):
            found[current["Id"]] = dict(current)

    for line in text.splitlines():
        if not line.strip():
            flush()
            current = {}
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        current[key] = value
    flush()
    return found


def _parse_int(value: str | None):
    if not value or value.startswith("["):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def sample_usage(apps: list) -> None:
    units = []
    for spec in apps:
        for unit in spec["units"]:
            if unit not in units:
                units.append(unit)
    per_unit: dict = {}
    if units:
        try:
            result = subprocess.run(
                ["systemctl", "show", *units, "-p", "Id", "-p", "ActiveState", "-p", "CPUUsageNSec", "-p", "MemoryCurrent"],
                capture_output=True,
                text=True,
                timeout=8,
                check=False,
            )
            per_unit = _parse_show(result.stdout or "")
        except (OSError, subprocess.TimeoutExpired):
            per_unit = {}
    now = time.time()
    summaries = {}
    for spec in apps:
        cpu_ns = 0
        mem = 0
        cpu_ok = False
        for unit in spec["units"]:
            info = per_unit.get(unit) or {}
            active = info.get("ActiveState") == "active"
            usage = _parse_int(info.get("CPUUsageNSec"))
            memory = _parse_int(info.get("MemoryCurrent"))
            if usage is not None:
                cpu_ns += usage
                cpu_ok = True
            elif not active:
                cpu_ok = True
            mem += memory if memory is not None else 0
        sample = {"t": now, "cpu_ns": cpu_ns if cpu_ok else None, "mem": mem}
        history = _usage_history.setdefault(spec["id"], deque(maxlen=400))
        history.append(sample)
        window = [item for item in history if now - item["t"] <= HOUR]
        points = [item for item in window if item["cpu_ns"] is not None]
        cpu_percent = None
        if len(points) >= 2:
            start = points[0]
            for previous, item in zip(points, points[1:]):
                if item["cpu_ns"] < previous["cpu_ns"]:
                    start = item
            elapsed = points[-1]["t"] - start["t"]
            delta = points[-1]["cpu_ns"] - start["cpu_ns"]
            if elapsed > 0 and delta >= 0:
                cpu_percent = round(delta / elapsed / 1e9 * 100, 1)
        mem_values = [item["mem"] for item in window]
        mem_bytes = round(sum(mem_values) / len(mem_values)) if mem_values else None
        summaries[spec["id"]] = {"cpu_percent": cpu_percent, "mem_bytes": mem_bytes}
    with _lock:
        _usage_now.update(summaries)


def usage_for(app_id: str) -> dict:
    with _lock:
        found = _usage_now.get(app_id)
        return dict(found) if found else {"cpu_percent": None, "mem_bytes": None}


def check_http(url: str, timeout: float = 1.5) -> tuple[str, int | None, int | None]:
    started = time.perf_counter()
    try:
        response = requests.get(url, timeout=(1.0, timeout), allow_redirects=False, stream=True, headers=HEADERS)
        try:
            code = response.status_code
        finally:
            response.close()
        elapsed = int((time.perf_counter() - started) * 1000)
    except requests.RequestException:
        return "down", None, None
    if 200 <= code < 400:
        return "up", elapsed, code
    return "down", elapsed, code


def _systemctl(*args: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", *args],
            capture_output=True,
            text=True,
            timeout=4,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return (result.stdout or "").strip()


def unit_enabled(name: str) -> bool:
    return _systemctl("is-enabled", name) == "enabled"


def unit_active(name: str) -> str:
    text = _systemctl("is-active", name)
    if text == "active":
        return "up"
    if text == "activating":
        return "unknown"
    if text in {"inactive", "failed", "deactivating"}:
        return "down"
    return "unknown"


def light_status(unit: str) -> str:
    if not unit_enabled(unit):
        return "off"
    return unit_active(unit)


def probe_app(spec: dict) -> dict:
    enabled = unit_enabled(spec["unit"])
    lights = [
        {"id": light["id"], "name": light["name"], "status": light_status(light["unit"])}
        for light in spec.get("lights", [])
    ]
    latency = None
    code = None
    slow = False
    if not enabled:
        status = "off"
    elif spec.get("target"):
        status, latency, code = check_http(spec["target"])
        slow = latency is not None and latency >= SLOW_MS and status == "up"
    else:
        status = unit_active(spec["unit"])
    return {
        "id": spec["id"],
        "name": spec["name"],
        "blurb": spec["blurb"],
        "url": spec["url"],
        "status": status,
        "enabled": enabled,
        "latency_ms": latency,
        "code": code,
        "slow": slow,
        "switch_note": spec.get("switch_note") or "",
        "lights": lights,
        "usage": usage_for(spec["id"]),
    }


def probe_all() -> list:
    apps = load_apps()
    with ThreadPoolExecutor(max_workers=8) as pool:
        jobs = [pool.submit(probe_app, spec) for spec in apps]
        return [job.result() for job in jobs]


def set_power(spec: dict, turn_on: bool) -> str | None:
    action = "on" if turn_on else "off"
    for unit in spec["units"]:
        try:
            result = subprocess.run(
                ["sudo", "-n", POWER_BIN, action, unit],
                capture_output=True,
                text=True,
                timeout=40,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return type(exc).__name__
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "systemctl failed").strip()
            return detail[-180:]
    return None


def _load_disk_feeds() -> None:
    try:
        saved = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(saved, dict):
        github = saved.get("github")
        youtube = saved.get("youtube")
        if isinstance(github, list) and github and isinstance(github[0], dict) and "repos" in github[0]:
            _feeds["github"] = github
            _feeds["github_at"] = saved.get("github_at") or 0
        if isinstance(youtube, list) and youtube and isinstance(youtube[0], dict) and "videos" in youtube[0]:
            _feeds["youtube"] = youtube
            _feeds["youtube_at"] = saved.get("youtube_at") or 0


def _save_disk_feeds() -> None:
    try:
        CACHE_PATH.write_text(json.dumps(_feeds), encoding="utf-8")
    except OSError:
        return


GITHUB_TOPICS = (
    ("homelab", "Homelab", "topic:homelab"),
    ("selfhosted", "Self-hosted", "topic:selfhosted"),
    ("home-assistant", "Home Assistant", "topic:home-assistant"),
    ("proxmox", "Proxmox", "topic:proxmox"),
    ("ai", "AI", ("topic:llm", "topic:generative-ai")),
)

YOUTUBE_CHANNELS = (
    ("NetworkChuck", "UC9x0AN7BWHpCDHSm9NiJFJQ"),
    ("Techno Tim", "UCOk-gHyjcWZNj3Br4oxwh0A"),
    ("Lawrence Systems", "UCHkYOD-3fZbuGhwsADBd9ZQ"),
    ("DB Tech", "UCVy16RS5eEDh8anP8j94G2A"),
    ("Christian Lempa", "UCZNhwA1B5YqiY1nLzmM0ZRg"),
    ("Wolfgang's Channel", "UCsnGwSIHyoYN0kiINAGUKxg"),
)


def _topic_queries() -> list[tuple[str, str, tuple]]:
    rows = []
    for topic, label, query in GITHUB_TOPICS:
        queries = query if isinstance(query, tuple) else (query,)
        rows.append((topic, label, queries))
    return rows


def _respect_search_limit(response: requests.Response) -> None:
    remaining = response.headers.get("X-RateLimit-Remaining")
    if remaining is None or not str(remaining).isdigit() or int(remaining) > 1:
        time.sleep(0.3)
        return
    reset = response.headers.get("X-RateLimit-Reset")
    delay = 15
    if reset and str(reset).isdigit():
        delay = max(1, int(reset) - time.time() + 1)
    time.sleep(min(delay, 70))


def _github_search(query: str) -> dict:
    found = {}
    response = None
    for _attempt in (1, 2):
        try:
            response = requests.get(
                "https://api.github.com/search/repositories",
                params={"q": query, "sort": "stars", "order": "desc", "per_page": 5},
                headers={**HEADERS, "Accept": "application/vnd.github+json"},
                timeout=8,
            )
        except requests.RequestException:
            return found
        if response.status_code in {403, 429}:
            _respect_search_limit(response)
            continue
        break
    if response is None or response.status_code != 200:
        return found
    for repo in response.json().get("items", []):
        url = repo.get("html_url") or ""
        if not url:
            continue
        found[url] = {
            "name": repo.get("name") or "repo",
            "full_name": repo.get("full_name") or "",
            "url": url,
            "description": (repo.get("description") or "")[:180],
            "stars": repo.get("stargazers_count") or 0,
            "language": repo.get("language") or "",
        }
    _respect_search_limit(response)
    return found


def _top_repos(found: dict, limit: int) -> list:
    return sorted(found.values(), key=lambda repo: repo["stars"], reverse=True)[:limit]


def _github() -> list | None:
    created_after = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=90)).strftime("%Y-%m-%d")
    all_time: dict = {}
    rising: dict = {}
    groups = []
    for topic, label, queries in _topic_queries():
        topic_repos: dict = {}
        topic_rising: dict = {}
        for query in queries:
            found = _github_search(query)
            topic_repos.update(found)
            all_time.update(found)
            gained = _github_search(f"{query} created:>{created_after}")
            topic_rising.update(gained)
            rising.update(gained)
        if topic_repos:
            groups.append({
                "id": topic,
                "label": label,
                "note": "All-time stars",
                "repos": _top_repos(topic_repos, 5),
            })
    if not all_time and not rising:
        return None
    lead = []
    if all_time:
        lead.append({
            "id": "stars",
            "label": "Stars",
            "note": "Most stars, all time",
            "repos": _top_repos(all_time, 8),
        })
    if rising:
        lead.append({
            "id": "rising",
            "label": "Rising",
            "note": "Created in the last 90 days, by stars",
            "repos": _top_repos(rising, 8),
        })
    return lead + groups


def _channel_videos(name: str, channel_id: str) -> list:
    try:
        feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")
    except Exception:
        return []
    videos = []
    for entry in feed.entries[:3]:
        video_id = getattr(entry, "yt_videoid", "") or (entry.id.split(":")[-1] if getattr(entry, "id", "") else "")
        videos.append({
            "channel": name,
            "title": entry.title,
            "url": entry.link,
            "published": getattr(entry, "published", ""),
            "video_id": video_id,
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg" if video_id else "",
        })
    return videos


def _youtube() -> list | None:
    channels = []
    for name, channel_id in YOUTUBE_CHANNELS:
        videos = _channel_videos(name, channel_id)
        if videos:
            channels.append({"name": name, "videos": videos})
    return channels or None


def refresh_feeds(force: bool = False) -> dict:
    now = time.time()
    changed = False
    if force or now - float(_feeds.get("github_at") or 0) > FEED_TTL:
        repos = _github()
        if repos is not None:
            _feeds["github"] = repos
            _feeds["github_at"] = now
            changed = True
    if force or now - float(_feeds.get("youtube_at") or 0) > FEED_TTL:
        videos = _youtube()
        if videos is not None:
            _feeds["youtube"] = videos
            _feeds["youtube_at"] = now
            changed = True
    if changed:
        _save_disk_feeds()
    return {"github": list(_feeds.get("github") or []), "youtube": list(_feeds.get("youtube") or [])}


def _update_snap(**parts) -> None:
    with _lock:
        _snap.update(parts)
        _snap["checked_at"] = time.time()
        if _snap.get("host") is not None:
            _snap["ready"] = True


def _host_loop() -> None:
    previous = _read_cpu()
    while True:
        time.sleep(HOST_EVERY)
        try:
            current = _read_cpu()
            latest = read_host(_cpu_percent(previous, current))
            latest["cpu_total"], latest["cpu_idle"] = current
            previous = current
            _host_samples.append((time.time(), latest))
            sample_usage(load_apps())
            _update_snap(host=host_view(latest))
        except Exception as exc:
            print("host sample error:", exc)


def _loop() -> None:
    while True:
        try:
            with _work_lock:
                services = probe_all()
            feeds = refresh_feeds()
            _update_snap(services=services, feeds=feeds)
        except Exception as exc:
            print("probe loop error:", exc)
        time.sleep(PROBE_EVERY)


def start() -> None:
    global _started
    if _started:
        return
    _started = True
    _load_disk_feeds()
    threading.Thread(target=_host_loop, name="homelab-host", daemon=True).start()
    threading.Thread(target=_loop, name="homelab-probes", daemon=True).start()


def snapshot() -> dict:
    with _lock:
        return copy.deepcopy(_snap)


@app.get("/api/overview")
def overview():
    return JSONResponse(snapshot(), headers={"Cache-Control": "no-store"})


@app.post("/api/services/{service_id}/power")
def power(service_id: str, payload: dict = Body(...)):
    spec = apps_by_id().get(service_id)
    if spec is None or not isinstance(payload, dict) or "on" not in payload:
        return JSONResponse({"error": "Unknown service"}, status_code=404)
    turn_on = bool(payload.get("on"))
    with _work_lock:
        error = set_power(spec, turn_on)
        updated = probe_app(spec)
        with _lock:
            current = _snap.get("services") or []
            _snap["services"] = [updated if item.get("id") == service_id else item for item in current]
            _snap["checked_at"] = time.time()
    if error:
        return JSONResponse({"error": "The switch did not finish", "detail": error, "service": updated}, status_code=500)
    return JSONResponse(updated, headers={"Cache-Control": "no-store"})


@app.get("/api/github")
def github():
    return JSONResponse(snapshot()["feeds"]["github"], headers={"Cache-Control": "no-store"})


@app.get("/api/youtube")
def youtube():
    return JSONResponse(snapshot()["feeds"]["youtube"], headers={"Cache-Control": "no-store"})


@app.get("/api/reddit")
def reddit():
    return JSONResponse([], headers={"Cache-Control": "no-store"})


frontend_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))


@app.get("/")
def index_page():
    return FileResponse(
        os.path.join(frontend_dist, "index.html"),
        headers={"Cache-Control": "no-store"},
    )


if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="static")

start()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8088)

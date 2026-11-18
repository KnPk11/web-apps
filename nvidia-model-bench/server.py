#!/usr/bin/env python3
"""NVIDIA Build latency bench.

Uses the key in .env when the browser does not send one. The key is never
written back in a response.
"""

import json
import os
import re
import socketserver
import ssl
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import SimpleHTTPRequestHandler
from urllib.parse import urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PORT = 8585
MODELS_URL = "https://integrate.api.nvidia.com/v1/models"
CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
MODEL_ID = re.compile(r"^[A-Za-z0-9_./:-]{3,160}$")
MAX_MODELS = 16
MAX_PROMPT = 500
MAX_TOKENS_CAP = 128

env_path = os.path.join(BASE_DIR, ".env")
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))

_catalog_lock = threading.Lock()
_catalog = {"at": 0.0, "models": []}


def server_key() -> str:
    return os.environ.get("NVIDIA_API_KEY", "").strip()


def categorize(model_id: str) -> str:
    low = model_id.lower()
    if any(bit in low for bit in ("guard", "safety", "translate", "riva", "calibration", "topic-control")):
        return "other"
    if any(bit in low for bit in ("code", "coder", "starcoder", "codestral")):
        return "code"
    if any(bit in low for bit in ("reason", "ultra", "super", "glm", "gemma-4", "kimi", "deepseek")):
        return "reasoning"
    return "chat"


def display_name(model_id: str) -> str:
    return model_id.split("/")[-1].replace("-", " ")


def fetch_live_models(api_key: str) -> list:
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    request = urllib.request.Request(MODELS_URL, headers=headers)
    with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=12) as response:
        payload = json.loads(response.read().decode("utf-8"))
    models = []
    for item in payload.get("data", []):
        model_id = item.get("id") or ""
        if not model_id or not MODEL_ID.match(model_id):
            continue
        low = model_id.lower()
        if any(bit in low for bit in ("embed", "rerank", "clip", "parse", "detector", "diffusion", "deplot")):
            continue
        provider = model_id.split("/")[0] if "/" in model_id else "nvidia"
        models.append({
            "id": model_id,
            "name": display_name(model_id),
            "provider": provider,
            "category": categorize(model_id),
        })
    models.sort(key=lambda model: (model["category"], model["id"]))
    return models


def catalog(api_key: str, force: bool = False) -> list:
    now = time.time()
    with _catalog_lock:
        if not force and _catalog["models"] and now - _catalog["at"] < 600:
            return list(_catalog["models"])
    try:
        models = fetch_live_models(api_key)
    except Exception as exc:
        print("model catalog error:", type(exc).__name__)
        with _catalog_lock:
            return list(_catalog["models"])
    with _catalog_lock:
        if models:
            _catalog["models"] = models
            _catalog["at"] = now
        return list(_catalog["models"] or models)


def _blank(model_id: str, code: int, error: str) -> dict:
    return {
        "model": model_id,
        "ok": False,
        "statusCode": code,
        "error": error[:180],
        "ttft_ms": None,
        "total_ms": None,
        "tokens": None,
        "tokens_estimated": False,
        "tps": None,
        "snippet": "",
    }


def _read_stream(response):
    ttft = None
    first_at = None
    text = ""
    reasoning = ""
    usage_tokens = None
    started = time.perf_counter()
    while True:
        raw = response.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="ignore").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            break
        if not data:
            continue
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            continue
        usage = chunk.get("usage") or {}
        if usage.get("completion_tokens"):
            usage_tokens = int(usage["completion_tokens"])
        choices = chunk.get("choices") or []
        delta = choices[0].get("delta", {}) if choices else {}
        piece = delta.get("content") or ""
        thought = delta.get("reasoning_content") or delta.get("reasoning") or ""
        if piece or thought:
            if ttft is None:
                first_at = time.perf_counter()
                ttft = (first_at - started) * 1000
            text += piece
            reasoning += thought
    finished = time.perf_counter()
    return ttft, first_at, finished, started, text, reasoning, usage_tokens


def probe_model(model_id: str, api_key: str, prompt: str, max_tokens: int) -> dict:
    if not api_key:
        return _blank(model_id, 401, "No API key on this machine")
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    request = urllib.request.Request(
        CHAT_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "text/event-stream",
        },
    )
    try:
        with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=35) as response:
            ttft, first_at, finished, started, text, reasoning, usage_tokens = _read_stream(response)
            status = response.status
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="ignore")
            parsed = json.loads(detail)
            detail = parsed.get("detail") or parsed.get("message") or parsed.get("error") or detail
            if isinstance(detail, dict):
                detail = detail.get("message") or json.dumps(detail)
        except Exception:
            detail = exc.reason
        if exc.code in (400, 422) and "stream_options" in str(detail).lower():
            return probe_model_plain(model_id, api_key, prompt, max_tokens)
        label = {401: "Key rejected", 404: "Model not found", 429: "Rate limited", 503: "Busy"}.get(exc.code, "Request failed")
        return _blank(model_id, exc.code, f"{label}. {detail}".strip())
    except Exception as exc:
        return _blank(model_id, 0, f"{type(exc).__name__}")

    total_ms = (finished - started) * 1000
    shown = (text or reasoning).strip().replace("\n", " ")
    estimated = usage_tokens is None
    tokens = usage_tokens
    if tokens is None and shown:
        tokens = max(1, round(len(shown) / 4))
    gen_seconds = (finished - first_at) if first_at else total_ms / 1000
    if gen_seconds < 0.05:
        gen_seconds = total_ms / 1000
    tps = round(tokens / gen_seconds, 1) if tokens and gen_seconds > 0 else None
    return {
        "model": model_id,
        "ok": bool(shown or tokens),
        "statusCode": status,
        "error": "" if shown or tokens else "Empty reply",
        "ttft_ms": round(ttft, 1) if ttft is not None else None,
        "total_ms": round(total_ms, 1),
        "tokens": tokens,
        "tokens_estimated": estimated and tokens is not None,
        "tps": tps,
        "snippet": shown[:160],
    }


def probe_model_plain(model_id: str, api_key: str, prompt: str, max_tokens: int) -> dict:
    """Retry once without stream_options for models that reject it."""
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0.2,
        "stream": True,
    }
    request = urllib.request.Request(
        CHAT_URL,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
    )
    try:
        with urllib.request.urlopen(request, context=ssl.create_default_context(), timeout=35) as response:
            ttft, first_at, finished, started, text, reasoning, usage_tokens = _read_stream(response)
    except urllib.error.HTTPError as exc:
        return _blank(model_id, exc.code, f"HTTP {exc.code}")
    except Exception as exc:
        return _blank(model_id, 0, type(exc).__name__)
    total_ms = (finished - started) * 1000
    shown = (text or reasoning).strip().replace("\n", " ")
    tokens = usage_tokens if usage_tokens is not None else (max(1, round(len(shown) / 4)) if shown else None)
    gen_seconds = (finished - first_at) if first_at else total_ms / 1000
    if gen_seconds < 0.05:
        gen_seconds = max(total_ms / 1000, 0.001)
    return {
        "model": model_id,
        "ok": bool(shown or tokens),
        "statusCode": 200,
        "error": "" if shown or tokens else "Empty reply",
        "ttft_ms": round(ttft, 1) if ttft is not None else None,
        "total_ms": round(total_ms, 1),
        "tokens": tokens,
        "tokens_estimated": usage_tokens is None and tokens is not None,
        "tps": round(tokens / gen_seconds, 1) if tokens else None,
        "snippet": shown[:160],
    }


def opencode_fragment(model_ids: list) -> dict:
    return {
        "provider": {
            "nvidia": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "NVIDIA Build",
                "options": {
                    "baseURL": "https://integrate.api.nvidia.com/v1",
                    "apiKey": "nvapi-YOUR_KEY",
                },
                "models": {model_id: {"name": display_name(model_id)} for model_id in model_ids},
            }
        }
    }


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        super().end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/status":
            self._send_json({"key": bool(server_key())})
            return
        if path == "/api/models":
            self._send_json({"models": catalog(server_key())})
            return
        super().do_GET()

    def do_POST(self):
        path = urlparse(self.path).path
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > 100_000:
            self._send_json({"error": "Request too large"}, 413)
            return
        raw = self.rfile.read(length).decode("utf-8") if length else "{}"
        try:
            data = json.loads(raw or "{}")
        except json.JSONDecodeError:
            data = {}
        if not isinstance(data, dict):
            data = {}

        if path == "/api/models":
            self._send_json({"models": catalog(self._key(data), force=True)})
            return
        if path == "/api/probe":
            self._probe(data)
            return
        if path == "/api/opencode-config":
            models = [model for model in data.get("models", []) if isinstance(model, str) and MODEL_ID.match(model)]
            self._send_json({
                "config": opencode_fragment(models[:MAX_MODELS]),
                "note": "Merge this provider into the existing OpenCode file. Do not replace the whole file.",
            })
            return
        self.send_error(404, "Not found")

    def _key(self, data: dict) -> str:
        sent = data.get("apiKey")
        if isinstance(sent, str) and sent.strip():
            return sent.strip()
        return server_key()

    def _probe(self, data: dict):
        api_key = self._key(data)
        requested = data.get("models") if isinstance(data.get("models"), list) else []
        models = []
        for model in requested:
            if isinstance(model, str) and MODEL_ID.match(model) and model not in models:
                models.append(model)
            if len(models) >= MAX_MODELS:
                break
        prompt = data.get("prompt") if isinstance(data.get("prompt"), str) else ""
        prompt = prompt.strip()[:MAX_PROMPT] or "Reply with exactly five words about backups."
        try:
            max_tokens = int(data.get("maxTokens") or 48)
        except (TypeError, ValueError):
            max_tokens = 48
        max_tokens = max(16, min(MAX_TOKENS_CAP, max_tokens))

        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self._event({"type": "start", "total": len(models)})
        if not models:
            self._event({"type": "end", "timestamp": time.strftime("%H:%M:%S")})
            return

        done = 0
        with ThreadPoolExecutor(max_workers=min(4, len(models))) as pool:
            futures = [pool.submit(probe_model, model, api_key, prompt, max_tokens) for model in models]
            for future in as_completed(futures):
                done += 1
                if not self._event({"type": "result", "done": done, "total": len(models), "result": future.result()}):
                    return
        self._event({"type": "end", "timestamp": time.strftime("%H:%M:%S")})

    def _event(self, payload: dict) -> bool:
        try:
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode("utf-8"))
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError):
            return False

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        message = fmt % args
        if "apiKey" in message or "nvapi-" in message:
            return
        super().log_message("%s", message)


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


if __name__ == "__main__":
    os.makedirs(STATIC_DIR, exist_ok=True)
    httpd = None
    for _ in range(20):
        try:
            httpd = Server(("", PORT), Handler)
            break
        except OSError:
            time.sleep(0.3)
    if httpd is None:
        raise SystemExit(f"port {PORT} stayed busy")
    print(f"NVIDIA bench listening on 0.0.0.0:{PORT}")
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()

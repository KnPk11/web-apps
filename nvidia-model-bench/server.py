#!/usr/bin/env python3
import http.server
import socketserver
import json
import urllib.request
import urllib.error
import ssl
import time
import os
import concurrent.futures
from urllib.parse import parse_qs, urlparse

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")

# ── Environment Auto-loader (.env) ─────────────────────────────
env_path = os.path.join(BASE_DIR, '.env')
if os.path.exists(env_path):
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

PORT = 8585
NVIDIA_MODELS_URL = "https://integrate.api.nvidia.com/v1/models"
NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

DEFAULT_MODELS = [
    {"id": "qwen/qwen2.5-coder-32b-instruct", "category": "coding", "name": "Qwen 2.5 Coder 32B"},
    {"id": "deepseek-ai/deepseek-v4-flash", "category": "reasoning", "name": "DeepSeek V4 Flash"},
    {"id": "deepseek-ai/deepseek-v4-pro", "category": "reasoning", "name": "DeepSeek V4 Pro"},
    {"id": "meta/llama-3.3-70b-instruct", "category": "general", "name": "Llama 3.3 70B Instruct"},
    {"id": "meta/codellama-70b", "category": "coding", "name": "CodeLlama 70B"},
    {"id": "google/gemma-4-31b-it", "category": "reasoning", "name": "Gemma 4 31B IT"},
    {"id": "google/codegemma-7b", "category": "coding", "name": "CodeGemma 7B"},
    {"id": "nv-mistralai/mistral-nemo-12b-instruct", "category": "coding", "name": "Mistral Nemo 12B"},
    {"id": "mistralai/codestral-22b-instruct-v0.1", "category": "coding", "name": "Codestral 22B"},
    {"id": "mistralai/mistral-large-2-instruct", "category": "reasoning", "name": "Mistral Large 2"},
    {"id": "ibm/granite-34b-code-instruct", "category": "coding", "name": "IBM Granite 34B Code"},
    {"id": "nvidia/llama-3.3-nemotron-super-49b-v1.5", "category": "reasoning", "name": "Nemotron Super 49B"},
    {"id": "nvidia/nemotron-3-ultra-550b-a55b", "category": "reasoning", "name": "Nemotron 3 Ultra 550B"},
    {"id": "z-ai/glm-5.2", "category": "reasoning", "name": "GLM 5.2"},
    {"id": "moonshotai/kimi-k2.6", "category": "reasoning", "name": "Kimi K2.6"}
]

def fetch_live_models(api_key=None):
    """Fetch live model catalog from NVIDIA Build API"""
    ctx = ssl.create_default_context()
    req = urllib.request.Request(NVIDIA_MODELS_URL)
    if api_key:
        req.add_header("Authorization", f"Bearer {api_key.strip()}")
    
    try:
        with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            raw_models = data.get("data", [])
            model_list = []
            for m in raw_models:
                m_id = m.get("id", "")
                if not m_id:
                    continue
                if any(x in m_id.lower() for x in ["embed", "rerank", "clip", "parse", "detector", "diffusion", "deplot", "fuyu", "kosmos"]):
                    continue
                
                cat = "general"
                if any(x in m_id.lower() for x in ["code", "coder", "codestral"]):
                    cat = "coding"
                elif any(x in m_id.lower() for x in ["r1", "v4", "reason", "glm", "gemma-4", "ultra", "super", "kimi"]):
                    cat = "reasoning"
                
                model_list.append({
                    "id": m_id,
                    "name": m_id.split("/")[-1].replace("-", " ").title(),
                    "provider": m_id.split("/")[0] if "/" in m_id else "nvidia",
                    "category": cat
                })
            return model_list if model_list else DEFAULT_MODELS
    except Exception as e:
        print(f"Error fetching live models: {e}")
        return DEFAULT_MODELS

def test_model_latency(model_id, api_key, prompt="Respond in 5 words with a quick coding tip."):
    """Benchmark a single model for TTFT, TPS, and status"""
    if not api_key:
        return {
            "model": model_id,
            "status": "API Key Required",
            "statusCode": 401,
            "ttft": 99999,
            "tps": 0,
            "totalTime": 99999,
            "responseSnippet": "Please enter your NVIDIA API Key above to run live benchmarks."
        }
    
    ctx = ssl.create_default_context()
    payload = json.dumps({
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 30,
        "temperature": 0.2,
        "stream": True
    }).encode("utf-8")
    
    req = urllib.request.Request(NVIDIA_CHAT_URL, data=payload, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key.strip()}"
    })
    
    start_time = time.perf_counter()
    ttft = None
    first_token_time = None
    tokens_count = 0
    response_text = ""
    status_str = "Healthy (200 OK)"
    status_code = 200

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=25) as resp:
            status_code = resp.status
            for line in resp:
                line_str = line.decode("utf-8", errors="ignore").strip()
                if not line_str.startswith("data:"):
                    continue
                if line_str == "data: [DONE]":
                    break
                
                json_str = line_str[5:].strip()
                if not json_str:
                    continue
                
                try:
                    chunk = json.loads(json_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        if ttft is None:
                            first_token_time = time.perf_counter()
                            ttft = (first_token_time - start_time) * 1000.0  # ms
                        tokens_count += 1
                        response_text += content
                except Exception:
                    pass
        
        end_time = time.perf_counter()
        total_time_ms = (end_time - start_time) * 1000.0
        
        gen_duration = (end_time - first_token_time) if first_token_time else 0.001
        tps = (tokens_count / gen_duration) if tokens_count > 0 else 0.0

        return {
            "model": model_id,
            "status": status_str,
            "statusCode": status_code,
            "ttft": round(ttft, 1) if ttft is not None else round(total_time_ms, 1),
            "tps": round(tps, 1),
            "totalTime": round(total_time_ms, 1),
            "responseSnippet": response_text.strip()[:100]
        }
    except urllib.error.HTTPError as e:
        err_msg = f"HTTP {e.code}: {e.reason}"
        if e.code == 429:
            err_msg = "Rate Limited / Queue Heavy (429)"
        elif e.code == 401:
            err_msg = "Invalid API Key (401)"
        elif e.code == 503:
            err_msg = "Service Overloaded (503)"
        
        return {
            "model": model_id,
            "status": err_msg,
            "statusCode": e.code,
            "ttft": 99999,
            "tps": 0,
            "totalTime": 99999,
            "responseSnippet": ""
        }
    except Exception as e:
        return {
            "model": model_id,
            "status": "Timeout / Network Error",
            "statusCode": 500,
            "ttft": 99999,
            "tps": 0,
            "totalTime": 99999,
            "responseSnippet": ""
        }

class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/models":
            api_key = os.getenv("NVIDIA_API_KEY", None)
            models = fetch_live_models(api_key)
            self._send_json({"models": models})
        else:
            super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"
        
        try:
            data = json.loads(body)
        except Exception:
            data = {}

        if parsed.path == "/api/fetch-models":
            api_key = data.get("apiKey", "").strip() or os.getenv("NVIDIA_API_KEY", "")
            models = fetch_live_models(api_key)
            self._send_json({"models": models})
        
        elif parsed.path == "/api/probe":
            api_key = data.get("apiKey", "").strip() or os.getenv("NVIDIA_API_KEY", "")
            target_models = data.get("models", [])
            prompt = data.get("prompt", "Write a python function to check prime numbers.")
            
            if not target_models:
                target_models = [m["id"] for m in DEFAULT_MODELS[:6]]

            results = []
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(12, len(target_models))) as executor:
                future_to_model = {
                    executor.submit(test_model_latency, m_id, api_key, prompt): m_id 
                    for m_id in target_models
                }
                for future in concurrent.futures.as_completed(future_to_model):
                    res = future.result()
                    results.append(res)

            results.sort(key=lambda x: x["ttft"])
            self._send_json({"results": results, "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")})
        
        elif parsed.path == "/api/opencode-config":
            selected_models = data.get("models", ["qwen/qwen2.5-coder-32b-instruct"])
            config = {
                "$schema": "https://opencode.ai/config.json",
                "provider": {
                  "nvidia-build": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "NVIDIA Build API",
                    "options": {
                      "baseURL": "https://integrate.api.nvidia.com/v1",
                      "apiKey": "nvapi-YOUR_NVIDIA_API_KEY"
                    },
                    "models": {
                      m: {"name": m.split("/")[-1]} for m in selected_models
                    }
                  }
                }
            }
            self._send_json({"config": config})
        else:
            self.send_error(404, "Endpoint not found")

    def _send_json(self, obj, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(obj).encode("utf-8"))

def run():
    os.makedirs(STATIC_DIR, exist_ok=True)
    with socketserver.TCPServer(("", PORT), RequestHandler) as httpd:
        print(f"NVIDIA Model Responsiveness Benchmark app running at http://0.0.0.0:{PORT}")
        httpd.serve_forever()

if __name__ == "__main__":
    run()

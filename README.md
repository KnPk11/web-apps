# Web Applications Suite

A repository showcasing lightweight, high-performance, vibe-coded web applications and dashboards for homelab monitoring, telemetry, ambient quiet-hours portals, and interactive AI tools.

## Repository Structure

```text
web-apps/
├── homelab-dashboard/    # Real-time Homelab Command Center (Reddit, GitHub, YouTube feeds)
├── night-time/           # Calm static video & portal for quiet hours (youtube-nocookie)
├── nvidia-model-bench/   # Real-time NVIDIA Build / NIM API LLM latency & TPS benchmark
├── story-cards/          # Unhinged interactive stories feed with AI LLM moderation & reviews
└── system-usage-stats/   # Real-time system telemetry & resource dashboard
```

## Architecture & Security Guidelines

### Reverse Proxy & Domain Privacy
* **Domain Name Isolation**: Domain names (`*.example.com`), SSL certificates, and external routing belong exclusively in your reverse proxy configuration (e.g. Caddyfile), never inside application code repositories.
* **Vite Host Restrictions**: Use `allowedHosts: true` in `vite.config.ts` to allow Vite and FastAPI to accept host headers dynamically from any reverse proxy.
* **Private IP Addresses (RFC 1918)**: While internal IPs (e.g. `192.168.x.x`) are non-routable over the public internet and safe from external access, keeping code IP-agnostic ensures your applications remain portable across subnets without breaking.

---

## Included Applications

### 1. Homelab Command Center (`homelab-dashboard/`)
* **Tech Stack**: FastAPI (Python), React 19, TypeScript, Vite, Lucide Icons.
* **Features**:
  * Live Reddit trends feed (`r/homelab`) with fallback resilience.
  * Real-time GitHub trending repositories (30-day active window highlighting top homelab & AI/LLM projects).
  * YouTube tech video feed (NetworkChuck terminal integration).
  * Single-process deployment: FastAPI mounts and serves the static production React frontend on port `8088`.
* **Quick Start**: `cd homelab-dashboard && ./run.sh` (Runs on `http://localhost:8088`)

---

### 2. Night-Time (`night-time/`)
* **Tech Stack**: HTML5, Vanilla JavaScript, HSL Dark Glassmorphic CSS, `youtube-nocookie` embeds.
* **Features**:
  * Quiet-hours ambient portal with curated video categories (*Homelab & Infra*, *Documentaries*, *Late Night Code*, *Ambient & Chill*).
  * Privacy-focused YouTube embeds (`youtube-nocookie.com`).
  * Search bar, category filter pills, `localStorage` favorites list, keyboard navigation (`Escape` to close modal).
* **Quick Start**: `cd night-time && python3 -m http.server 5173` (Runs on `http://localhost:5173`)

---

### 3. NVIDIA Model Responsiveness Benchmark (`nvidia-model-bench/`)
* **Tech Stack**: Python (`http.server`, `concurrent.futures`), Vanilla JavaScript, CSS HSL design system, SOPS secrets management.
* **Features**:
  * Live concurrency probing of NVIDIA Build / NIM endpoints (TTFT - Time To First Token, TPS - Tokens Per Second, total latency).
  * Auto-fetching active model catalogs directly from `integrate.api.nvidia.com`.
  * Secure client-side `localStorage` key management & SOPS encrypted fallback (`secrets.sops.env`).
  * 1-click OpenCode configuration generator.
* **Quick Start**: `cd nvidia-model-bench && ./run.sh` (Runs on `http://localhost:8585`)

---

### 4. Story Cards (`story-cards/`)
* **Tech Stack**: Python (`http.server`, `sqlite3`), Vanilla JS, HTML5, LLM API integration, SOPS secrets management.
* **Features**:
  * Interactive story submissions, voting, and comments.
  * AI-powered unhinged reviews and moderation with exponential backoff & rate-limiting resilience.
  * SOPS-encrypted secrets management (`secrets.sops.env` encrypted with Age keys).
* **Quick Start**: `cd story-cards && ./run.sh` (Runs on `http://localhost:33363`)

---

### 5. System Usage Stats (`system-usage-stats/`)
* **Tech Stack**: Python (`psutil`, `http.server`), Vanilla JavaScript, HTML5 Canvas, HSL Glassmorphism CSS.
* **Features**:
  * Real-time telemetry: CPU utilisation, per-core metrics, virtual & swap memory, disk partitions, load average, system uptime.
  * Standalone dashboard interface.
* **Quick Start**: `cd system-usage-stats && python3 -m http.server 8000` (Runs on `http://localhost:8000`)

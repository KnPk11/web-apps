import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import "./App.css";

type Status = "up" | "down" | "off" | "unknown";

interface Host {
  hostname: string;
  model: string;
  cores: number;
  uptime_s: number;
  load: number[];
  cpu_percent: number | null;
  mem_percent: number;
  mem_used_gb: number;
  mem_total_gb: number;
  swap_percent: number;
  disk_percent: number;
  disk_used_gb: number;
  disk_total_gb: number;
  temp_c: number | null;
  procs: number;
  averages: {
    m5: AverageWindow;
    m10: AverageWindow;
    m30: AverageWindow;
  };
}

interface AverageWindow {
  cpu: number | null;
  mem: number | null;
  disk: number | null;
  temp: number | null;
}

interface Light {
  id: string;
  name: string;
  status: Status;
}

interface Service {
  id: string;
  name: string;
  blurb: string;
  url: string | null;
  status: Status;
  enabled: boolean;
  latency_ms: number | null;
  code: number | null;
  slow: boolean;
  switch_note: string;
  lights: Light[];
  usage: { cpu_percent: number | null; mem_bytes: number | null };
}

interface Repo {
  name: string;
  full_name: string;
  url: string;
  description: string;
  stars: number;
  language: string;
}

interface GitGroup {
  id: string;
  label: string;
  note?: string;
  repos: Repo[];
}

interface Video {
  channel: string;
  title: string;
  url: string;
  published: string;
  thumbnail: string;
}

interface Channel {
  name: string;
  videos: Video[];
}

interface Overview {
  ready: boolean;
  host: Host | null;
  services: Service[];
  feeds: { github: GitGroup[]; youtube: Channel[] };
  checked_at: number | null;
}

function uptime(seconds: number) {
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function age(unix: number | null, now: number) {
  if (!unix) return "waiting";
  const seconds = Math.max(0, Math.round(now / 1000 - unix));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  return `${Math.floor(seconds / 60)}m ago`;
}

function meterClass(percent: number) {
  if (percent >= 85) return "hot";
  if (percent >= 70) return "warn";
  return "";
}

function tempClass(temp: number) {
  if (temp >= 80) return "hot";
  if (temp >= 65) return "warm";
  return "";
}

function statusLabel(service: Service) {
  if (service.status === "off") return "Off";
  if (service.status === "down") return service.code ? `Down · ${service.code}` : "Down";
  if (service.status === "unknown") return "Checking";
  if (service.slow) return "Slow";
  return "Up";
}

type SortKey = "default" | "name" | "type" | "cpu" | "ram" | "status" | "latency";

const SORTS: Array<{ key: SortKey; label: string; defaultDir: 1 | -1 }> = [
  { key: "name", label: "Name", defaultDir: 1 },
  { key: "type", label: "Type", defaultDir: 1 },
  { key: "cpu", label: "1h CPU", defaultDir: -1 },
  { key: "ram", label: "1h RAM", defaultDir: -1 },
  { key: "status", label: "Status", defaultDir: 1 },
  { key: "latency", label: "Latency", defaultDir: 1 },
];

const STATUS_RANK: Record<Status, number> = { down: 0, up: 1, unknown: 2, off: 3 };

function compareServices(a: Service, b: Service, key: SortKey = "default", dir: 1 | -1 = 1) {
  const byName = a.name.localeCompare(b.name, undefined, { sensitivity: "base", numeric: true });
  const num = (x: number | null | undefined, y: number | null | undefined) => {
    // Missing values always sink to the bottom, whichever direction is chosen.
    if (x == null && y == null) return 0;
    if (x == null) return 1;
    if (y == null) return -1;
    return (x - y) * dir;
  };
  let result = 0;
  if (key === "name") return byName * dir;
  if (key === "type") result = a.blurb.localeCompare(b.blurb, undefined, { sensitivity: "base" }) * dir;
  else if (key === "cpu") result = num(a.usage?.cpu_percent, b.usage?.cpu_percent);
  else if (key === "ram") result = num(a.usage?.mem_bytes, b.usage?.mem_bytes);
  else if (key === "status") result = (STATUS_RANK[a.status] - STATUS_RANK[b.status]) * dir;
  else if (key === "latency") result = num(a.latency_ms, b.latency_ms);
  else {
    const cpuA = a.usage?.cpu_percent ?? -1;
    const cpuB = b.usage?.cpu_percent ?? -1;
    if (cpuA !== cpuB) return cpuB - cpuA;
    const ramA = a.usage?.mem_bytes ?? -1;
    const ramB = b.usage?.mem_bytes ?? -1;
    if (ramA !== ramB) return ramB - ramA;
  }
  return result || byName;
}

function matches(query: string, parts: Array<string | null | undefined>) {
  const needle = query.trim().toLowerCase();
  if (!needle) return true;
  return parts.some((part) => (part || "").toLowerCase().includes(needle));
}

function when(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short" });
}

function AvgBars({ metric, averages }: { metric: keyof AverageWindow; averages: Host["averages"] }) {
  const rows = [
    ["5m", averages.m5],
    ["10m", averages.m10],
    ["30m", averages.m30],
  ] as const;
  return (
    <div className="avgs">
      {rows.map(([label, window]) => {
        const value = window[metric];
        const width = value == null ? 0 : Math.max(0, Math.min(100, metric === "temp" ? value : value));
        const text = value == null ? "—" : metric === "temp" ? `${Math.round(value)}°` : `${Math.round(value)}%`;
        const tone = metric === "temp" ? tempClass(value ?? 0) : meterClass(value ?? 0);
        return (
          <div className="avg" key={label}>
            <span>{label}</span>
            <div className={`bar ${tone}`}>
              <span style={{ width: `${width}%` }} />
            </div>
            <b>{text}</b>
          </div>
        );
      })}
    </div>
  );
}

function formatBytes(value: number | null) {
  if (value == null) return "—";
  if (value <= 0) return "0 MB";
  if (value < 1024 * 1024) return `${Math.max(1, Math.round(value / 1024))} KB`;
  if (value < 1024 ** 3) return `${Math.round(value / 1024 ** 2)} MB`;
  return `${(value / 1024 ** 3).toFixed(1)} GB`;
}

function lightLabel(status: Status) {
  if (status === "up") return "Up";
  if (status === "off") return "Off";
  if (status === "down") return "Down";
  return "Checking";
}

function ServiceCard({ service, busy, onToggle }: { service: Service; busy: boolean; onToggle: (service: Service) => void }) {
  return (
    <article className={`tile ${service.status}${service.slow ? " slow" : ""}`}>
      <div className="toprow">
        {service.url ? (
          <a className="name" href={service.url} target="_blank" rel="noreferrer">{service.name}</a>
        ) : (
          <span className="name">{service.name}</span>
        )}
        <span className="ms">{service.latency_ms === null ? "" : `${service.latency_ms} ms`}</span>
        <label className="switch">
          <input
            type="checkbox"
            checked={service.enabled}
            disabled={busy}
            aria-label={`${service.enabled ? "Switch off" : "Switch on"} ${service.name}`}
            onChange={() => onToggle(service)}
          />
          <span />
        </label>
      </div>
      <div className="staterow">
        <span className="state">{statusLabel(service)}</span>
        {service.lights.length > 0 && (
          <div className="lights">
            {service.lights.map((light) => (
              <span key={light.id} className={`light ${light.status}`} title={`${light.name}: ${lightLabel(light.status)}`}>
                <i />
                {light.name}
              </span>
            ))}
          </div>
        )}
      </div>
      <span className="blurb">{service.blurb}</span>
      <p className="usage">
        1h {service.usage?.cpu_percent == null ? "—" : `${service.usage.cpu_percent}%`} CPU
        · {formatBytes(service.usage?.mem_bytes ?? null)} RAM
      </p>
    </article>
  );
}

function App() {
  const [data, setData] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  const [query, setQuery] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("default");
  const [sortDir, setSortDir] = useState<1 | -1>(1);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState<string | null>(null);
  const [gitTopic, setGitTopic] = useState("stars");
  const [channel, setChannel] = useState("all");

  useEffect(() => {
    let alive = true;

    async function load() {
      if (document.hidden) return;
      setRefreshing(true);
      try {
        const response = await fetch("/api/overview", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const body = (await response.json()) as Overview;
        if (!alive) return;
        setData(body);
        setError("");
      } catch {
        if (!alive) return;
        setError("Status feed did not answer.");
      } finally {
        if (alive) setRefreshing(false);
      }
    }

    void load();
    const poll = window.setInterval(() => void load(), 10000);
    const clock = window.setInterval(() => setNow(Date.now()), 1000);
    const onVis = () => {
      if (!document.hidden) void load();
    };
    const onKey = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing = target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA");
      if (event.key === "/" && !typing) {
        event.preventDefault();
        document.getElementById("q")?.focus();
      }
      if (event.key === "Escape") setQuery("");
    };
    document.addEventListener("visibilitychange", onVis);
    window.addEventListener("keydown", onKey);
    return () => {
      alive = false;
      window.clearInterval(poll);
      window.clearInterval(clock);
      document.removeEventListener("visibilitychange", onVis);
      window.removeEventListener("keydown", onKey);
    };
  }, []);

  async function toggle(service: Service) {
    const next = !service.enabled;
    const verb = next ? "Switch on" : "Switch off";
    const extra = service.switch_note ? ` ${service.switch_note}` : "";
    if (!window.confirm(`${verb} ${service.name}? It stays that way after a reboot.${extra}`)) return;
    setBusy(service.id);
    setError("");
    try {
      const response = await fetch(`/api/services/${service.id}/power`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ on: next }),
      });
      const body = (await response.json()) as Service & { error?: string; service?: Service };
      const updated = body.service || body;
      if (!response.ok || !updated.id) {
        setError(body.error || `Could not switch ${service.name}.`);
      }
      if (updated.id) {
        setData((current) => current ? {
          ...current,
          services: current.services.map((item) => item.id === updated.id ? updated : item),
        } : current);
      }
    } catch {
      setError(`Could not switch ${service.name}.`);
    } finally {
      setBusy(null);
    }
  }

  const services = data?.services ?? [];
  const visible = useMemo(
    () => services.filter((service) => matches(query, [service.name, service.blurb, service.url])).sort((a, b) => compareServices(a, b, sortKey, sortDir)),
    [services, query, sortKey, sortDir],
  );
  const live = visible.filter((service) => service.status !== "off");
  const offline = visible.filter((service) => service.status === "off");
  const offlineRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    if (query.trim() && offlineRef.current) offlineRef.current.open = true;
  }, [query]);
  const down = services.filter((service) => service.status === "down" && service.url);
  const up = services.filter((service) => service.status === "up").length;
  const off = services.filter((service) => service.status === "off").length;
  const gitGroups = data?.feeds.github ?? [];
  const selectedGit = gitGroups.find((group) => group.id === gitTopic) ?? gitGroups[0];
  const repos = (selectedGit?.repos ?? []).filter((repo) =>
    matches(query, [repo.name, repo.full_name, repo.description, repo.language, selectedGit?.label]),
  );
  const ytChannels = data?.feeds.youtube ?? [];
  const videos = (channel === "all"
    ? ytChannels.flatMap((item) => item.videos)
    : ytChannels.find((item) => item.name === channel)?.videos ?? []
  ).filter((video) => matches(query, [video.title, video.channel])).sort((a, b) => Date.parse(b.published) - Date.parse(a.published));
  const gitPanelRef = useRef<HTMLDivElement>(null);
  const ytPanelRef = useRef<HTMLDivElement>(null);
  const ytListRef = useRef<HTMLDivElement>(null);
  const showReading = !query || repos.length > 0 || videos.length > 0;
  const host = data?.host;

  // Show as many videos as fit within the height of the GitHub panel.
  useLayoutEffect(() => {
    function fit() {
      const git = gitPanelRef.current;
      const yt = ytPanelRef.current;
      const list = ytListRef.current;
      if (!git || !yt || !list) return;
      const rows = Array.from(list.querySelectorAll<HTMLElement>(".video-row"));
      rows.forEach((row) => { row.style.display = ""; });
      const stacked = yt.getBoundingClientRect().top >= git.getBoundingClientRect().bottom - 1;
      if (stacked) {
        rows.forEach((row, index) => { row.style.display = index < 4 ? "" : "none"; });
        return;
      }
      const limit = git.getBoundingClientRect().bottom - 6;
      let shown = 0;
      rows.forEach((row) => {
        if (shown > 0 && row.getBoundingClientRect().bottom > limit) row.style.display = "none";
        else shown += 1;
      });
    }
    fit();
    window.addEventListener("resize", fit);
    return () => window.removeEventListener("resize", fit);
  });

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <span className="mark" aria-hidden="true" />
          <div>
            <p className="kicker">Homelab</p>
            <h1>Command centre</h1>
          </div>
        </div>
        <div className="hostline">
          <strong>{host?.hostname || "This machine"}</strong>
          <span>
            {host ? `${host.model} · ${host.cores} threads · up ${uptime(host.uptime_s)}` : "Reading the host"}
          </span>
        </div>
        <div className="clock">
          <time dateTime={new Date(now).toISOString()}>
            {new Date(now).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
          </time>
          <span>
            {new Date(now).toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" })}
            {" · "}
            {age(data?.checked_at ?? null, now)}
          </span>
        </div>
      </header>

      <p className="vital-note">Live reading from the last 15 seconds, sampled every 10.</p>
      {host ? (
        <section className="vitals" aria-label="This machine">
          <article className="meter">
            <p className="label">CPU</p>
            <span className={`value ${meterClass(host.cpu_percent ?? 0)}`}>
              {host.cpu_percent === null ? "—" : `${Math.round(host.cpu_percent)}%`}
            </span>
            <span className="sub">load {host.load.map((item) => item.toFixed(2)).join("  ")}</span>
            <AvgBars metric="cpu" averages={host.averages} />
          </article>
          <article className="meter">
            <p className="label">Memory</p>
            <span className={`value ${meterClass(host.mem_percent)}`}>{host.mem_percent}%</span>
            <span className="sub">
              {host.mem_used_gb.toFixed(1)} / {host.mem_total_gb.toFixed(1)} GB
              {host.swap_percent >= 15 ? ` · swap ${host.swap_percent}%` : ""}
            </span>
            <AvgBars metric="mem" averages={host.averages} />
          </article>
          <article className="meter">
            <p className="label">Disk</p>
            <span className={`value ${meterClass(host.disk_percent)}`}>{host.disk_percent}%</span>
            <span className="sub">
              {host.disk_used_gb.toFixed(1)} / {host.disk_total_gb.toFixed(1)} GB on /
            </span>
            <AvgBars metric="disk" averages={host.averages} />
          </article>
          <article className="meter">
            <p className="label">Temperature</p>
            <span className={`value ${host.temp_c === null ? "" : tempClass(host.temp_c)}`}>
              {host.temp_c === null ? "—" : `${Math.round(host.temp_c)}°`}
            </span>
            <span className="sub">{host.procs} processes</span>
            <AvgBars metric="temp" averages={host.averages} />
          </article>
        </section>
      ) : (
        <section className="vitals" aria-hidden="true">
          <div className="skel" />
          <div className="skel" />
          <div className="skel" />
          <div className="skel" />
        </section>
      )}

      {error && <p className="error">{error}</p>}

      {down.length > 0 && !query && (
        <div className="banner" role="status">
          <strong>{down.length === 1 ? `${down[0].name} is not answering` : `${down.length} web apps on this machine are not answering`}</strong>
          <ul>
            {down.map((service) => (
              <li key={service.id}>
                <a href={service.url || "#"} target="_blank" rel="noreferrer">
                  {service.name}
                </a>
                {service.code ? ` · HTTP ${service.code}` : " · no reply"}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="toolbar">
        <div className="pills" aria-live="polite">
          <span className={down.length ? "pill" : "pill ok"}>{up} up</span>
          {down.length > 0 && <span className="pill bad">{down.length} down</span>}
          {off > 0 && <span className="pill">{off} off</span>}
        </div>
        <div className="sorts" role="group" aria-label="Sort apps">
          <span className="sorts-label">Sort</span>
          {SORTS.map((item) => {
            const active = sortKey === item.key;
            return (
              <button
                key={item.key}
                type="button"
                className={active ? "sort active" : "sort"}
                aria-pressed={active}
                title={active ? "Click again to reverse" : `Sort by ${item.label}`}
                onClick={() => {
                  if (active) setSortDir((sortDir * -1) as 1 | -1);
                  else {
                    setSortKey(item.key);
                    setSortDir(item.defaultDir);
                  }
                }}
              >
                {item.label}
                {active ? <span className="arrow">{sortDir === 1 ? " ↑" : " ↓"}</span> : null}
              </button>
            );
          })}
          {sortKey !== "default" && (
            <button type="button" className="sort reset" onClick={() => { setSortKey("default"); setSortDir(1); }} title="Back to busiest first">
              Reset
            </button>
          )}
        </div>
        <input
          id="q"
          className="search"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="Search apps"
          aria-label="Search apps"
          autoComplete="off"
          spellCheck={false}
        />
        <button className="refresh" type="button" disabled={refreshing} onClick={() => window.location.reload()}>
          {refreshing ? "Checking" : "Reload"}
        </button>
      </div>

      {data?.ready ? (
        <section className="group">
          <h2>
            This machine <span>{live.length}</span>
          </h2>
          {live.length === 0 && offline.length === 0 ? <p className="empty">Nothing matches that search.</p> : null}
          {live.length > 0 && (
            <div className="grid">
              {live.map((service) => (
                <ServiceCard key={service.id} service={service} busy={busy === service.id} onToggle={(item) => void toggle(item)} />
              ))}
            </div>
          )}
          {offline.length > 0 && (
            <details className="offline" ref={offlineRef}>
              <summary>
                Offline <span>{offline.length}</span>
              </summary>
              <div className="grid">
                {offline.map((service) => (
                  <ServiceCard key={service.id} service={service} busy={busy === service.id} onToggle={(item) => void toggle(item)} />
                ))}
              </div>
            </details>
          )}
        </section>
      ) : (
        <div className="grid" style={{ marginTop: 16 }}>
          {Array.from({ length: 8 }, (_, index) => (
            <div className="skel" key={index} />
          ))}
        </div>
      )}

      {showReading && data?.ready && (
        <section className="reading">
          <div className="panel" ref={gitPanelRef}>
            <h2>GitHub</h2>
            <div className="chips">
              {gitGroups.map((group) => (
                <button
                  key={group.id}
                  type="button"
                  className={group.id === selectedGit?.id ? "chip on" : "chip"}
                  onClick={() => setGitTopic(group.id)}
                >
                  {group.label}
                </button>
              ))}
            </div>
            {selectedGit?.note && <p className="note">{selectedGit.note}</p>}
            <div className="feed">
              {repos.length === 0 && <p className="note">No matching repositories.</p>}
              {repos.map((repo) => (
                <a key={repo.url} href={repo.url} target="_blank" rel="noreferrer">
                  <div className="toprow">
                    <span className="title">{repo.full_name || repo.name}</span>
                    <span className="stars">{repo.stars.toLocaleString()}</span>
                  </div>
                  {repo.description && <p className="desc">{repo.description}</p>}
                  <div className="feed-meta">
                    <span>{repo.language || "Code"}</span>
                  </div>
                </a>
              ))}
            </div>
          </div>
          <div className="panel" ref={ytPanelRef}>
            <h2>YouTube</h2>
            <div className="chips">
              <button type="button" className={channel === "all" ? "chip on" : "chip"} onClick={() => setChannel("all")}>Latest</button>
              {ytChannels.map((item) => (
                <button
                  key={item.name}
                  type="button"
                  className={channel === item.name ? "chip on" : "chip"}
                  onClick={() => setChannel(item.name)}
                >
                  {item.name}
                </button>
              ))}
            </div>
            <div className="yt" ref={ytListRef}>
              {videos.length === 0 && <p className="note">No matching videos.</p>}
              {videos.map((video) => (
                <a key={video.url} className="video-row" href={video.url} target="_blank" rel="noreferrer">
                  {video.thumbnail && <img src={video.thumbnail} alt="" />}
                  <span>
                    <span className="title">{video.title}</span>
                    <span className="feed-meta">{video.channel} · {when(video.published)}</span>
                  </span>
                </a>
              ))}
            </div>
          </div>
        </section>
      )}

      <p className="foot">The large figures are the last 15 seconds. The bars are 5, 10, and 30 minute averages. Each card’s CPU and RAM line fills in over the past hour. A switch leaves that app off or on after a reboot. Press / to search.</p>
    </div>
  );
}

export default App;

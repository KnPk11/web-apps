import os
import datetime
import requests
import feedparser
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DEFAULT_REDDIT_POSTS = [
    {"title": "My 42U rack setup with Proxmox HA cluster and Mikrotik 10G networking", "url": "https://reddit.com/r/homelab", "score": 450, "thumbnail": None, "author": "homelaber_pro"},
    {"title": "Showoff Sunday: Custom 3D printed Raspberry Pi 5 rackmount server", "url": "https://reddit.com/r/homelab", "score": 320, "thumbnail": None, "author": "pi_builder"},
    {"title": "Migrated all services to Docker + Caddy reverse proxy with automatic SSL", "url": "https://reddit.com/r/homelab", "score": 280, "thumbnail": None, "author": "sysadmin_dev"},
    {"title": "How to optimize ZFS pools for low power consumption on mini PCs", "url": "https://reddit.com/r/homelab", "score": 195, "thumbnail": None, "author": "zfs_master"},
]

@app.get("/api/reddit")
def get_reddit_trends():
    try:
        url = "https://www.reddit.com/r/homelab/hot.rss"
        headers = {"User-Agent": "HomelabDashboardCustomApp/1.0 (by /u/homelab_user)"}
        feed = feedparser.parse(url, request_headers=headers)
        posts = []
        if feed.entries:
            for entry in feed.entries[:10]:
                author = getattr(entry, "author", "homelab").replace("/u/", "")
                posts.append({
                    "title": entry.title,
                    "url": entry.link,
                    "score": 120,
                    "thumbnail": None,
                    "author": author
                })
        return posts if posts else DEFAULT_REDDIT_POSTS
    except Exception as e:
        print("Error fetching Reddit:", e)
        return DEFAULT_REDDIT_POSTS

@app.get("/api/github")
def get_github_trends():
    try:
        date_30_days_ago = (datetime.datetime.now() - datetime.timedelta(days=30)).strftime("%Y-%m-%d")
        url = f"https://api.github.com/search/repositories?q=homelab+OR+ai+OR+llm+pushed:>{date_30_days_ago}+sort:stars&per_page=10"
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "HomelabDashboard/1.0"}
        response = requests.get(url, headers=headers, timeout=8)
        if response.status_code != 200:
            return []
        
        data = response.json()
        repos = []
        for repo in data.get("items", []):
            repos.append({
                "name": repo.get("name", "repo"),
                "full_name": repo.get("full_name", ""),
                "url": repo.get("html_url", "#"),
                "description": repo.get("description") or "",
                "stars": repo.get("stargazers_count", 0),
                "language": repo.get("language") or "Code"
            })
        return repos
    except Exception as e:
        print("Error fetching GitHub:", e)
        return []

@app.get("/api/youtube")
def get_youtube_videos():
    try:
        rss_url = "https://www.youtube.com/feeds/videos.xml?user=NetworkChuck"
        feed = feedparser.parse(rss_url)
        videos = []
        for entry in feed.entries[:6]:
            video_id = entry.id.split(":")[-1] if hasattr(entry, "id") else ""
            videos.append({
                "title": entry.title,
                "url": entry.link,
                "video_id": video_id,
                "published": getattr(entry, "published", ""),
                "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg" if video_id else ""
            })
        return videos
    except Exception as e:
        print("Error fetching YouTube:", e)
        return []

# Mount static frontend production build
frontend_dist = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend", "dist"))
if os.path.exists(frontend_dist):
    app.mount("/", StaticFiles(directory=frontend_dist, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8088)

import { useEffect, useState } from "react";
import { GitBranch, LayoutDashboard, MessageSquare, PlayCircle, Star } from "lucide-react";
import "./App.css";

interface RedditPost {
  title: string;
  url: string;
  score: number;
  thumbnail: string | null;
  author: string;
}

interface GithubRepo {
  name: string;
  full_name: string;
  url: string;
  description: string;
  stars: number;
  language: string;
}

interface YouTubeVideo {
  title: string;
  url: string;
  video_id: string;
  published: string;
  thumbnail: string;
}

function App() {
  const [redditPosts, setRedditPosts] = useState<RedditPost[]>([]);
  const [githubRepos, setGithubRepos] = useState<GithubRepo[]>([]);
  const [youtubeVideos, setYoutubeVideos] = useState<YouTubeVideo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [redditRes, githubRes, youtubeRes] = await Promise.all([
          fetch(`/api/reddit`).catch(() => null),
          fetch(`/api/github`).catch(() => null),
          fetch(`/api/youtube`).catch(() => null),
        ]);

        const redditData = redditRes && redditRes.ok ? await redditRes.json() : [];
        const githubData = githubRes && githubRes.ok ? await githubRes.json() : [];
        const youtubeData = youtubeRes && youtubeRes.ok ? await youtubeRes.json() : [];

        setRedditPosts(Array.isArray(redditData) ? redditData : []);
        setGithubRepos(Array.isArray(githubData) ? githubData : []);
        setYoutubeVideos(Array.isArray(youtubeData) ? youtubeData : []);
      } catch (error) {
        console.error("Error fetching data:", error);
      } finally {
        setLoading(false);
      }
    };

    fetchData();
  }, []);

  if (loading) {
    return (
      <div className="dashboard-container">
        <div className="loading">Initializing Neural Interface...</div>
      </div>
    );
  }

  return (
    <div className="dashboard-container">
      <header className="header">
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          <LayoutDashboard size={32} color="#58a6ff" />
          <h1>Homelab Command Center</h1>
        </div>
        <div style={{ color: "#8b949e", fontSize: "0.9rem" }}>
          Updated: {new Date().toLocaleTimeString()}
        </div>
      </header>

      <div className="grid-layout">
        {/* Reddit Column */}
        <section className="column">
          <div className="column-header">
            <MessageSquare size={20} color="#ff4500" />
            <span>Reddit Trends (r/homelab)</span>
          </div>
          {redditPosts.map((post, i) => (
            <a href={post.url} target="_blank" rel="noopener noreferrer" key={i}>
              <div className="card">
                <h3>{post.title}</h3>
                <div className="card-meta">
                  <span>u/{post.author}</span>
                  <span className="score-badge">+{post.score}</span>
                </div>
              </div>
            </a>
          ))}
        </section>

        {/* GitHub Column */}
        <section className="column">
          <div className="column-header">
            <GitBranch size={20} />
            <span>GitHub Trending</span>
          </div>
          {githubRepos.map((repo, i) => (
            <a href={repo.url} target="_blank" rel="noopener noreferrer" key={i}>
              <div className="card">
                <h3>{repo.name}</h3>
                <p>
                  {repo.description?.substring(0, 100)}
                  {repo.description?.length > 100 ? "..." : ""}
                </p>
                <div className="card-meta">
                  <span>{repo.language || "Unknown"}</span>
                  <span className="stars-badge">
                    <Star size={12} /> {repo.stars?.toLocaleString() || 0}
                  </span>
                </div>
              </div>
            </a>
          ))}
        </section>

        {/* YouTube Column */}
        <section className="column">
          <div className="column-header">
            <PlayCircle size={20} color="#ff0000" />
            <span>NetworkChuck Terminal</span>
          </div>
          {youtubeVideos.map((video, i) => (
            <a href={video.url} target="_blank" rel="noopener noreferrer" key={i}>
              <div className="card video-card">
                <img src={video.thumbnail} alt={video.title} className="video-thumbnail" />
                <div className="video-info">
                  <h3>{video.title}</h3>
                  <div className="card-meta">
                    <span>{video.published ? new Date(video.published).toLocaleDateString() : ""}</span>
                  </div>
                </div>
              </div>
            </a>
          ))}
        </section>
      </div>
    </div>
  );
}

export default App;

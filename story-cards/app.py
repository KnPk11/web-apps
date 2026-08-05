import json
import sqlite3
import os
import time
import subprocess
from http.server import HTTPServer, BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from database import get_db, init_db

PORT = 33363
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'stories.db')

# Rate limiting state
ratelimits = {}

# Common archiver and screenshot bot user-agents
BOT_KEYWORDS = [
    'bot', 'spider', 'crawl', 'archiver', 'archive', 'screenshot', 'slurp', 
    'headless', 'chrome-lighthouse', 'wget', 'curl', 'python-requests',
    'ia_archiver', 'googlebot', 'bingbot', 'twitterbot', 'facebot',
    'discordbot', 'whatsapp', 'telegrambot'
]

def is_bot(ua):
    if not ua: return True
    ua = ua.lower()
    return any(k in ua for k in BOT_KEYWORDS)

# Global & per-IP rate limiting state
ratelimits = {}
global_history = {'story': [], 'comment': []}

def check_ratelimit(ip, action):
    now = time.time()
    
    # Global limit (Total server load protection)
    global_limit = 1 if action == 'story' else 10
    global_history[action] = [t for t in global_history[action] if now - t < 60]
    if len(global_history[action]) >= global_limit:
        return False

    # Per-IP limit
    if ip not in ratelimits:
        ratelimits[ip] = {'story': [], 'comment': []}
    ratelimits[ip][action] = [t for t in ratelimits[ip][action] if now - t < 60]
    limit = 1 if action == 'story' else 5
    if len(ratelimits[ip][action]) >= limit:
        return False
    
    ratelimits[ip][action].append(now)
    global_history[action].append(now)
    return True

# Helper for Point 2 (Mad Libs)
def apply_mad_libs(text):
    from review_worker import mad_libs_filter
    return mad_libs_filter(text)

# Helper for Point 3 (Moderation)
def moderate_sync(content, is_comment=False):
    from review_worker import moderate_content
    return moderate_content(content, is_comment)

class StoryHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlparse(self.path)
        if url.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            with open('index.html', 'rb') as f:
                self.wfile.write(f.read())
        elif url.path == '/robots.txt':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"User-agent: *\nDisallow: /\n")
        elif url.path == '/api/stories':
            ua = self.headers.get('User-Agent', '')
            user_is_bot = is_bot(ua)
            conn = get_db()
            cursor = conn.cursor()
            
            # If bot, ONLY show approved content. If human, show all (frontend filters)
            if user_is_bot:
                cursor.execute('SELECT * FROM stories WHERE moderated = 1 ORDER BY created_at DESC')
            else:
                cursor.execute('SELECT * FROM stories ORDER BY created_at DESC')
            
            stories = [dict(row) for row in cursor.fetchall()]
            # Add all comments for each story (newest first)
            for story in stories:
                if user_is_bot:
                    cursor.execute('SELECT id, nickname, comment, review, moderated, created_at FROM comments WHERE story_id = ? AND moderated = 1 ORDER BY created_at DESC', (story['id'],))
                else:
                    cursor.execute('SELECT id, nickname, comment, review, moderated, created_at FROM comments WHERE story_id = ? ORDER BY created_at DESC', (story['id'],))
                story['comments'] = [dict(row) for row in cursor.fetchall()]
            conn.close()
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(stories).encode())
        elif url.path == '/api/latest-podcast':
            pod_dir = '/home/k/web-apps/story-cards/podcasts'
            # Support both mp3 and m4a
            files = [f for f in os.listdir(pod_dir) if f.endswith('.mp3') or f.endswith('.m4a')]
            if not files:
                self.send_response(404); self.end_headers(); return
            # Get newest file by modification time
            latest = max([os.path.join(pod_dir, f) for f in files], key=os.path.getmtime)
            filename = os.path.basename(latest)
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'url': f'/podcasts/{filename}', 'filename': filename}).encode())

        elif url.path == '/api/export-script':
            conn = get_db(); cursor = conn.cursor()
            cursor.execute('SELECT nickname, content, review FROM stories WHERE moderated = 1 ORDER BY created_at DESC LIMIT 20')
            rows = cursor.fetchall()
            conn.close()
            
            script = "STORYCARDS COMMUNITY DIGEST\n========================\n\n"
            for r in rows:
                script += f"STORY BY @{r['nickname']}:\n{r['content']}\n\nMODERATOR'S TAKE:\n{r['review']}\n"
                script += "-"*30 + "\n\n"
            
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(script.encode())

        elif url.path.startswith('/podcasts/'):
            from urllib.parse import unquote
            filename = os.path.basename(unquote(url.path))
            filepath = os.path.join('/home/k/web-apps/story-cards/podcasts', filename)
            if os.path.exists(filepath):
                self.send_response(200)
                # Set correct mime type based on extension
                mime = 'audio/mp4' if filename.endswith('.m4a') else 'audio/mpeg'
                self.send_header('Content-type', mime)
                self.end_headers()
                with open(filepath, 'rb') as f:
                    self.wfile.write(f.read())
            else:
                self.send_error(404)
        else:
            self.send_error(404)

    def do_POST(self):
        url = urlparse(self.path)
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length).decode('utf-8')
        client_ip = self.client_address[0]
        data = json.loads(post_data)
        
        if url.path == '/api/submit':
            if not check_ratelimit(client_ip, 'story'):
                self.send_response(429); self.end_headers(); return
            
            nickname = data.get('nickname', 'Anonymous')[:25]
            content = data.get('content', '')[:5000]
            if content:
                conn = get_db(); cursor = conn.cursor()
                cursor.execute('INSERT INTO stories (nickname, content) VALUES (?, ?)', (nickname, content))
                
                # Keep last 100 stories only
                cursor.execute('''
                    DELETE FROM stories WHERE id NOT IN (
                        SELECT id FROM stories ORDER BY created_at DESC LIMIT 100
                    )
                ''')
                # Also cleanup orphan comments
                cursor.execute('''
                    DELETE FROM comments WHERE story_id NOT IN (SELECT id FROM stories)
                ''')
                
                conn.commit(); conn.close()
                self.send_response(201); self.end_headers()
        
        elif url.path == '/api/comment':
            if not check_ratelimit(client_ip, 'comment'):
                self.send_response(429); self.end_headers(); return
            
            story_id = data.get('story_id')
            nickname = data.get('nickname', 'Anonymous')[:25]
            comment = data.get('comment', '')[:200]
            if story_id and comment:
                conn = get_db(); cursor = conn.cursor()
                cursor.execute('INSERT INTO comments (story_id, nickname, comment) VALUES (?, ?, ?)', (story_id, nickname, comment))
                conn.commit(); conn.close()
                self.send_response(201); self.end_headers()

        elif url.path == '/api/rehabilitate':
            # Point 3: User edits their rejected content
            item_id = data.get('id')
            item_type = data.get('type') # 'story' or 'comment'
            new_text = data.get('content')
            nickname = data.get('nickname')

            conn = get_db(); cursor = conn.cursor()
            
            # 1. Moderation check
            status = moderate_sync(new_text, is_comment=(item_type == 'comment'))
            
            # 2. If STILL rejected, apply Mad Libs (Point 2)
            final_text = new_text
            if status == -1:
                final_text = apply_mad_libs(new_text)
                status = 1 # Force approve after filtering
            
            if item_type == 'story':
                cursor.execute('UPDATE stories SET content = ?, moderated = ?, review = NULL WHERE id = ? AND nickname = ?', (final_text, status, item_id, nickname))
            else:
                cursor.execute('UPDATE comments SET comment = ?, moderated = ?, review = NULL WHERE id = ? AND nickname = ?', (final_text, status, item_id, nickname))
            
            conn.commit(); conn.close()
            self.send_response(200); self.end_headers()

if __name__ == '__main__':
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    if not os.path.exists(DB_PATH): init_db()
    # Use ThreadingHTTPServer to handle multiple requests without hanging
    server = ThreadingHTTPServer(('0.0.0.0', PORT), StoryHandler)
    print(f'Multi-threaded Server running at http://localhost:{PORT}')
    server.serve_forever()

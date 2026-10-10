import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), 'stories.db')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    # Stories table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS stories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nickname TEXT NOT NULL,
        content TEXT NOT NULL,
        moderated INTEGER DEFAULT 0, -- 0: pending, 1: approved, -1: rejected
        review TEXT,
        upvotes INTEGER DEFAULT 0,
        is_ai INTEGER DEFAULT 0,
        source_timestamp TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # Check if upvotes, is_ai, and source_timestamp columns exist on stories
    cursor.execute("PRAGMA table_info(stories)")
    columns = [row['name'] for row in cursor.fetchall()]
    if 'upvotes' not in columns:
        cursor.execute("ALTER TABLE stories ADD COLUMN upvotes INTEGER DEFAULT 0")
    if 'is_ai' not in columns:
        cursor.execute("ALTER TABLE stories ADD COLUMN is_ai INTEGER DEFAULT 0")
    if 'source_timestamp' not in columns:
        cursor.execute("ALTER TABLE stories ADD COLUMN source_timestamp TEXT")

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stories_nickname ON stories (nickname)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_stories_source_ts ON stories (source_timestamp)")
    
    # Comments table
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS comments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        story_id INTEGER,
        nickname TEXT NOT NULL,
        comment TEXT NOT NULL,
        moderated INTEGER DEFAULT 0,
        review TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (story_id) REFERENCES stories (id)
    )
    ''')

    # Upvotes tracking table (prevents double upvoting and prevents unrating votes not cast by voter)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS story_upvotes (
        story_id INTEGER,
        voter_key TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (story_id, voter_key),
        FOREIGN KEY (story_id) REFERENCES stories (id)
    )
    ''')

    # Favourites backup table (preserves LAN and user favourites across browser cookie/cache resets)
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS user_bookmarks (
        user_key TEXT,
        story_id INTEGER,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (user_key, story_id),
        FOREIGN KEY (story_id) REFERENCES stories (id)
    )
    ''')
    
    conn.commit()
    conn.close()

if __name__ == '__main__':
    init_db()
    print(f"Database initialized at {DB_PATH}")

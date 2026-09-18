#!/usr/bin/env python3
"""
StoryCards Story Fetcher & Generator
Fetches real narrative stories from Reddit (r/tifu, r/confession, r/CasualConversation, etc.)
and inserts them into StoryCards stories.db.
Falls back to NVIDIA LLM generation if Reddit is unreachable or rate-limited.
"""

import os
import re
import json
import html
import random
import sqlite3
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime

# ── Paths & Config ─────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, 'stories.db')
ENV_PATH = os.path.join(BASE_DIR, '.env')

# Load .env if present
if os.path.exists(ENV_PATH):
    with open(ENV_PATH, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

NVIDIA_BASE_URL = os.environ.get('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1')
NVIDIA_API_KEY = os.environ.get('NVIDIA_API_KEY', '')
NVIDIA_MODEL = os.environ.get('NVIDIA_MODEL', 'nvidia/nemotron-3.5-lightning-30b-a3b')

SUBREDDITS = ['tifu', 'confession', 'CasualConversation', 'AmItheAsshole']
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 StoryCardsHomelab/1.0'

FUNNY_NICKNAMES = [
    "SarcasticPenguin", "CaptainUnderpants", "ExistentialCucumber",
    "AngryPotato", "DepressedUnicorn", "ProfessionalIdiot",
    "CaffeineAddict42", "CryingInTheClub", "LordOfTheSocks",
    "AnxiousAvocado", "SleepySloth", "ChaosMuffin",
    "CursedWithGoodLooks", "ProfessionalOverthinker",
    "TiredOfThisShit", "AccidentallyFamous", "CryingInTheShower",
    "EmotionalSupportGoblin", "CursedWithCharm", "ProfessionalMenace"
]

FALLBACK_PROMPTS = [
    "Write a funny, self-deprecating first-person story about a catastrophic cooking or baking fail.",
    "Write an absurd, real-sounding first-person story about an embarrassing mix-up at work or in a public setting.",
    "Write a hilarious first-person story about a DIY home improvement attempt that went completely wrong.",
    "Write a relatable and funny first-person story about a pet causing total chaos at the worst possible time.",
    "Write an entertaining first-person story about an accidental text or email blunder.",
]

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def is_story_duplicate(conn, title, content_snippet):
    """Check if story or similar content already exists in the database."""
    cur = conn.cursor()
    # Check by title prefix or content snippet
    query_pattern = f"%{content_snippet[:60]}%"
    cur.execute("SELECT id FROM stories WHERE content LIKE ? LIMIT 1", (query_pattern,))
    return cur.fetchone() is not None

def clean_reddit_text(raw_html):
    """Convert Reddit HTML content to clean, readable plain text."""
    if not raw_html:
        return ""
    
    # Extract selftext inside Reddit markdown container
    match = re.search(r'<!-- SC_OFF --><div class="md">(.*?)</div><!-- SC_ON -->', raw_html, re.DOTALL)
    body_html = match.group(1) if match else raw_html

    # Unescape HTML entities
    text = html.unescape(body_html)
    # Convert paragraph breaks and list items to newlines
    text = re.sub(r'</p>\s*<p>', '\n\n', text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<li[^>]*>', '• ', text)
    text = re.sub(r'</li>', '\n', text)
    # Strip any remaining tags
    text = re.sub(r'<[^>]+>', '', text)
    # Remove hyperlinks
    text = re.sub(r'https?://\S+', '', text)
    # Clean whitespace
    lines = [re.sub(r'[ \t]+', ' ', l).strip() for l in text.split('\n')]
    text = '\n'.join(l for l in lines if l)

    # Filter out common automated notices
    text = re.sub(r'(?i)I am a bot, and this action was performed automatically.*', '', text)
    text = re.sub(r'(?i)Please contact the moderators of this subreddit.*', '', text)
    return text.strip()

def fetch_reddit_story():
    """Attempt to fetch a narrative story from Reddit RSS."""
    subreddits = list(SUBREDDITS)
    random.shuffle(subreddits)

    conn = get_db()

    for sub in subreddits:
        url = f"https://www.reddit.com/r/{sub}/hot.rss"
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking r/{sub} ({url})...")
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read()

            root = ET.fromstring(xml_data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('atom:entry', ns)

            for entry in entries:
                title_el = entry.find('atom:title', ns)
                title = title_el.text.strip() if title_el is not None else ""
                
                # Skip meta / sticky / megathread / mod announcement posts
                title_lower = title.lower()
                if any(k in title_lower for k in ['[meta]', 'megathread', 'weekly', 'rules', 'reminder', 'announcement', 'community update', 'moderator', 'welcome', 'discussion thread']):
                    continue

                author_el = entry.find('atom:author/atom:name', ns)
                author = author_el.text.replace('/u/', '').strip() if author_el is not None else "anon"
                if author.lower() in ['automoderator', 'reddit', 'deleted']:
                    author = random.choice(FUNNY_NICKNAMES)

                content_el = entry.find('atom:content', ns)
                raw_content = content_el.text if content_el is not None else ""
                body = clean_reddit_text(raw_content)

                # Skip empty or removed content
                if not body or any(k in body.lower() for k in ['[removed]', '[deleted]']):
                    continue

                full_content = f"{title}\n\n{body}".strip()
                length = len(full_content)

                # Ideal length check (250 - 1500 chars)
                if length < 250:
                    continue

                if length > 1500:
                    # Clean truncation at last sentence before 1480 chars
                    truncated = full_content[:1480]
                    last_period = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
                    if last_period > 600:
                        full_content = truncated[:last_period + 1] + " [Continued...]"
                    else:
                        full_content = truncated.rstrip() + "..."

                # Check duplicate
                if is_story_duplicate(conn, title, full_content):
                    print(f"  Skipping duplicate: '{title[:50]}...'")
                    continue

                conn.close()
                print(f"Successfully fetched Reddit story from r/{sub} by @{author} ({len(full_content)} chars)")
                return author[:25], full_content

        except urllib.error.HTTPError as e:
            print(f"  HTTP error from r/{sub}: {e.code} ({e.reason})")
        except Exception as e:
            print(f"  Error fetching r/{sub}: {e}")

    conn.close()
    return None, None

def generate_ai_story():
    """Fallback: Generate an original, dynamic story via NVIDIA LLM."""
    if not NVIDIA_API_KEY:
        print("No NVIDIA API key configured for AI fallback.")
        return None, None

    prompt_theme = random.choice(FALLBACK_PROMPTS)
    full_prompt = (
        f"{prompt_theme}\n"
        "Requirements:\n"
        "- Write in first person (casual, humorous, Reddit TIFU / confession style).\n"
        "- Between 400 and 700 characters.\n"
        "- Believable and funny with a clear punchline.\n"
        "- Do NOT repeat formulas or hardcoded text.\n"
        "- Output ONLY the story text directly. No title, no thinking process, no draft notes."
    )

    payload = json.dumps({
        'model': NVIDIA_MODEL,
        'messages': [
            {'role': 'system', 'content': 'You are a witty, relatable internet storyteller. Output ONLY the story.'},
            {'role': 'user', 'content': full_prompt}
        ],
        'max_tokens': 600,
        'temperature': 0.85
    }).encode('utf-8')

    req = urllib.request.Request(
        f'{NVIDIA_BASE_URL}/chat/completions',
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {NVIDIA_API_KEY}',
            'User-Agent': 'StoryCardsFetcher/1.0'
        },
        method='POST'
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode('utf-8'))
        raw = data['choices'][0]['message']['content'].strip()

        # Clean reasoning if present
        text = raw
        if 'thinking process' in text.lower():
            paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
            candidates = [p for p in paragraphs if not re.match(r'^(here\'?s|1\.|2\.|3\.|4\.|5\.|\*|-|draft|selected)', p, re.I)]
            if candidates:
                text = candidates[-1]
        text = re.sub(r'^(Story|Draft|Attempt\s*\d*):?\s*', '', text, flags=re.I).strip('"\' ')

        if len(text) >= 200:
            nickname = random.choice(FUNNY_NICKNAMES)
            print(f"Generated fallback AI story ({len(text)} chars) as @{nickname}")
            return nickname, text
    except Exception as e:
        print(f"AI story generation fallback error: {e}")

    return None, None

def save_story(nickname, content):
    """Save the story to stories.db with moderated=0."""
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO stories (nickname, content, moderated)
        VALUES (?, ?, 0)
    """, (nickname, content))
    inserted_id = cur.lastrowid

    # Retain the newest 100 stories only
    cur.execute("""
        DELETE FROM stories WHERE id NOT IN (
            SELECT id FROM stories ORDER BY created_at DESC LIMIT 100
        )
    """)
    conn.commit()
    conn.close()
    print(f"Saved story #{inserted_id} by @{nickname} to {DB_PATH} with moderated=0")
    return inserted_id

def main():
    print(f"=== StoryCards Fetcher Started [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ===")
    
    # 1. Try fetching a real story from Reddit
    nickname, content = fetch_reddit_story()

    # 2. If Reddit was blocked or unavailable, fall back to AI generation
    if not content:
        print("Reddit fetch yielded no stories; falling back to dynamic AI generation...")
        nickname, content = generate_ai_story()

    # 3. Save to database if we have content
    if content and nickname:
        story_id = save_story(nickname, content)
        print(f"=== Successfully processed story #{story_id} ===")
    else:
        print("=== Warning: No story could be fetched or generated this cycle ===")

if __name__ == '__main__':
    main()

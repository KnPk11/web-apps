#!/usr/bin/env python3
"""
StoryCards Story Fetcher & Generator
Fetches real narrative stories from Reddit or generates them via NVIDIA LLM.
Features:
- Balanced 3-way split: 1/3 Finance (YOLO, loss porn, degenerate trades),
  1/3 Tech/IT/Engineering (work fails, production blunders, getting fired),
  and 1/3 Life Stories (TIFU, confessions, funny personal mishaps).
- Robust filtering to eliminate boring advice questions, ETF debates, retirement accounts,
  corporate due diligence, and tech support configuration questions.
- High-quality AI fallback adhering to the exact same 3-way split ratio.
"""

import os
import re
import json
import html
import time
import random
import sqlite3
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from difflib import SequenceMatcher

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
NVIDIA_MODEL = os.environ.get('NVIDIA_MODEL', 'nvidia/nemotron-3-ultra-550b-a55b')

# Financial & market loss subreddits (strictly YOLO / options / crypto / degenerate chaos)
FINANCIAL_SUBREDDITS = ['wallstreetbets', 'pennystocks', 'CryptoCurrency', 'options', 'SatoshiStreetBets']

# Tech, IT, and Engineering disaster subreddits (funny work fails, sysadmin screw-ups, getting fired)
TECH_SUBREDDITS = ['talesfromtechsupport', 'sysadmin', 'cscareerquestions']

# General narrative subreddits (real life confessions, mishaps, entertaining drama)
GENERAL_SUBREDDITS = ['tifu', 'confession', 'CasualConversation', 'AmItheAsshole', 'offmychest']

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 StoryCardsHomelab/1.0'

# Keywords indicating financial disaster, trading losses, or bad investment regrets
FINANCIAL_LOSS_KEYWORDS = [
    'loss', 'lost', 'saving', '0dte', 'yolo', 'margin', 'liquidat',
    'dip', 'option', 'call', 'put', 'gamble', 'broke', 'debt',
    'invest', 'penny stock', 'crypto', 'bitcoin', 'portfolio', 'down 9',
    'down 8', 'down 7', 'down 6', 'down 5', 'blew', 'all-in', 'rugpull',
    'rug pull', 'bankrupt', 'ruined', 'guh', 'wendy', 'retard', 'stupid',
    'regret', 'crash', 'tank', 'scam', 'short', 'leverag', 'wipeout',
    'wiped out', 'fucked up', 'rekt', 'bleeding'
]

# Keywords indicating tech, IT, engineering, and workplace screwups
TECH_FAIL_KEYWORDS = [
    'fired', 'job', 'boss', 'database', 'production', 'prod', 'server',
    'deploy', 'backup', 'rm -rf', 'drop table', 'outage', 'downtime',
    'aws', 'cloud', 'script', 'it ticket', 'sysadmin', 'root', 'ssh',
    'pipeline', 'git', 'pushed', 'credential', 'api key', 'secret',
    'crash', 'glitch', 'firewall', 'cable', 'dns', 'bgp', 'reply all',
    'email blast', 'intern', 'first day', 'hr', 'terminated', 'resigned',
    'disciplinary', 'incident', 'postmortem', 'fuckup', 'blunder',
    'disaster', 'catastrophe', 'unplugged', 'datacenter', 'data center',
    'escalat', 'pagerduty', 'on-call', 'broken', 'hotfix', 'staging',
    'tab completion', 'shredded'
]

# Patterns that indicate boring financial planning, retirement advice, ETF comparisons, and corporate DD
BORING_FINANCIAL_PATTERNS = [
    r'\b(ucits|etf|etfs|index fund|index funds|bogle|boglehead|roth ira|traditional ira|401k|403b|hsa|isa|sipp|tax drag|tax loss harvesting|expense ratio|dividend yield|dollar[- ]cost averag|dca|emergency fund|hysa|high[- ]yield savings|treasury bills?|t[- ]bills?|government bonds?|asset allocation|compound interest|financial planner|fiduciary|safe withdrawal rate|fire movement|vanguard|fidelity|schwab index)\b',
    r'\b(due diligence|bull thesis|bear thesis|operating income|ebitda|balance sheet|quarterly earnings|p/e ratio|valuation multiple|market cap analysis|financial statement|revenue growth|price target|analyst rating)\b'
]

# Patterns that indicate boring tech configuration questions, vendor comparisons, or interview prep
BORING_TECH_PATTERNS = [
    r'\b(which is the best|what should i use|any recommendations? for|help me choose|how do you configure|best practice for|looking for advice|how to setup|how do i setup|how do i configure|what ticketing system|vendor recommendations?|pricing for|licensing question|salary advice|resume review|prep for interview)\b'
]

# Patterns that indicate advice-seeking, questions, or generic discussion starters
ADVICE_QUESTION_PATTERNS = [
    r'^(which|what|where|how|why|should|is|can|could|would|will|any|does|do|has|have|are|rate my|advice on|need advice|thoughts on|seeking advice|recommendations? for|help with|question regarding)\b',
    r'\b(which is the best|what should i|should i buy|should i sell|how to invest|where to invest|how do i|is it worth it|can i afford|what do you think of|rate my portfolio|portfolio review|help me choose|need some advice|financial advice|any thoughts on|am i doing this right|any recommendations|looking for advice|best .* to buy)\b',
    r'^(what is your|what are your|tell me about|what was your|has anyone|anyone else|cmv:|change my view|unpopular opinion:?|discussion:?|question:?|poll:?)\b'
]

FUNNY_NICKNAMES = [
    "SarcasticPenguin", "CaptainUnderpants", "ExistentialCucumber",
    "AngryPotato", "DepressedUnicorn", "ProfessionalIdiot",
    "CaffeineAddict42", "CryingInTheClub", "LordOfTheSocks",
    "AnxiousAvocado", "SleepySloth", "ChaosMuffin",
    "CursedWithGoodLooks", "ProfessionalOverthinker",
    "TiredOfThisShit", "AccidentallyFamous", "CryingInTheShower",
    "EmotionalSupportGoblin", "CursedWithCharm", "ProfessionalMenace",
    "ConsoleCowboy", "BashCatastrophe", "ProductionSlayer"
]

FINANCIAL_FALLBACK_PROMPTS = [
    "Write a hilarious, self-deprecating first-person confession about losing a huge sum of money or life savings on a terribly thought-out stock options play or meme crypto investment.",
    "Write a funny and absurd first-person confession about trying to day trade while at work, getting margin called, and the resulting chaos.",
    "Write an entertaining first-person story about putting rent or tuition money into an obscure penny stock or crypto rugpull based on bad Reddit advice.",
    "Write a chaotic first-person story about accidentally placing a massive market order instead of a limit order on a volatile stock and immediately watching it tank.",
    "Write a hilarious first-person story about taking financial advice from a bizarre internet influencer or Discord group and losing everything on 0DTE options.",
    "Write a self-deprecating first-person story about hiding a catastrophic crypto liquidation from a spouse or partner while pretending everything is fine."
]

TECH_FALLBACK_PROMPTS = [
    "Write a hilarious, self-deprecating first-person confession about an IT sysadmin or junior software engineer who accidentally ran a destructive command (like dropping the production database or running rm -rf on the main server) on a Friday afternoon, causing total corporate chaos or nearly getting fired.",
    "Write an entertaining, relatable first-person confession about accidentally replying-all or sending a snarky internal Slack message complaining about executive leadership to the entire company.",
    "Write a funny, chaotic first-person story about pushing hardcoded production AWS credentials or API keys to a public GitHub repo or breaking the main payment gateway during peak shopping hours.",
    "Write an absurd first-person IT sysadmin confession about an automation script going completely rogue, accidentally wiping laptops, revoking company-wide access, or causing a massive office meltdown.",
    "Write a hilarious first-person tech support story about a disastrous hardware or network mistake—like unplugging the core corporate rack to plug in a vacuum cleaner or tripping a datacenter fire suppression system.",
    "Write a funny first-person confession from an engineer who bluffed their way through a deployment, took down the entire company infrastructure, and got escorted out of the building by HR."
]

LIFESTORY_FALLBACK_PROMPTS = [
    "Write an absurd, real-sounding first-person story about an embarrassing mix-up or misunderstanding in a public setting (like a gym, supermarket, or job interview).",
    "Write a relatable and funny first-person story about a catastrophic cooking, baking, or DIY home improvement fail that spiralled out of control.",
    "Write an entertaining first-person story about a petty neighbourhood feud or bizarre encounter with an eccentric stranger.",
    "Write a funny first-person confession about a grand, romantic or impressive gesture that failed completely and horribly in public.",
    "Write a hilarious first-person story about accidentally sending an unhinged text or email to the wrong person, like a boss or in-law."
]

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def normalise_title(text: str) -> str:
    """Strip emojis, markdown, punctuation, and extra whitespace for fuzzy matching."""
    if not text:
        return ""
    cleaned = re.sub(r'[^\w\s]', '', text.lower())
    return ' '.join(cleaned.split())

def is_story_duplicate(conn, title: str, full_content: str) -> bool:
    """
    Robust multi-layer duplicate detection against existing database stories:
    1. Exact & normalised fuzzy title matching (SequenceMatcher ratio >= 0.75).
    2. Title substring matching (title appearing within existing story or vice versa).
    3. Content snippet & opening sentence match.
    4. Significant vocabulary/word overlap (Jaccard similarity >= 0.55 on words >= 4 chars).
    5. Early content sequence similarity (SequenceMatcher ratio >= 0.65 on first 500 chars).
    """
    if not full_content:
        return False

    title_clean = title.strip() if title else ""
    norm_candidate_title = normalise_title(title_clean)
    candidate_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', full_content.lower()))

    cur = conn.cursor()
    existing_stories = cur.execute("SELECT id, content FROM stories ORDER BY id DESC LIMIT 100").fetchall()

    for sid, scontent in existing_stories:
        if not scontent:
            continue

        lines = [l.strip() for l in scontent.split('\n') if l.strip()]
        existing_title = lines[0] if lines else ""
        norm_existing_title = normalise_title(existing_title)

        # Layer 1: Title match (exact or fuzzy)
        if norm_candidate_title and norm_existing_title:
            if norm_candidate_title == norm_existing_title:
                return True
            if SequenceMatcher(None, norm_candidate_title, norm_existing_title).ratio() >= 0.75:
                return True

        # Layer 2: Title substring containment (for meaningful titles >= 15 chars)
        if len(title_clean) >= 15 and title_clean.lower() in scontent.lower():
            return True
        if len(existing_title) >= 15 and existing_title.lower() in full_content.lower():
            return True

        # Layer 3: Opening text match
        if len(full_content) >= 60 and full_content[:60].lower() in scontent.lower():
            return True
        if len(scontent) >= 60 and scontent[:60].lower() in full_content.lower():
            return True

        # Layer 4: Word overlap (Jaccard similarity on 4+ letter words)
        existing_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', scontent.lower()))
        if candidate_words and existing_words:
            overlap = len(candidate_words & existing_words) / max(len(candidate_words | existing_words), 1)
            if overlap >= 0.55:
                return True

        # Layer 5: Sequence similarity on the first 500 characters
        content_sim = SequenceMatcher(None, full_content[:500].lower(), scontent[:500].lower()).ratio()
        if content_sim >= 0.65:
            return True

    return False

def clean_reddit_text(raw_html):
    """Convert Reddit HTML content to clean, readable plain text."""
    if not raw_html:
        return ""
    
    match = re.search(r'<!-- SC_OFF --><div class="md">(.*?)</div><!-- SC_ON -->', raw_html, re.DOTALL)
    body_html = match.group(1) if match else raw_html

    text = html.unescape(body_html)
    text = re.sub(r'</p>\s*<p>', '\n\n', text)
    text = re.sub(r'<br\s*/?>', '\n', text)
    text = re.sub(r'<li[^>]*>', '• ', text)
    text = re.sub(r'</li>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'https?://\S+', '', text)
    lines = [re.sub(r'[ \t]+', ' ', l).strip() for l in text.split('\n')]
    text = '\n'.join(l for l in lines if l)

    text = re.sub(r'(?i)I am a bot, and this action was performed automatically.*', '', text)
    text = re.sub(r'(?i)Please contact the moderators of this subreddit.*', '', text)
    return text.strip()

def is_candidate_story_valid(title: str, body: str, subreddit: str, category: str) -> tuple[bool, str]:
    """
    Screens candidate posts to filter out boring advice requests, ETF comparisons,
    corporate DD, tech help questions, and ensures personal narrative flavour.
    """
    title_clean = title.strip()
    full_text = f"{title_clean}\n\n{body.strip()}".strip()
    title_lower = title_clean.lower()

    # 1. Skip sticky, meta, megathread, mod announcements
    meta_keywords = [
        '[meta]', 'megathread', 'weekly', 'daily discussion', 'rules', 'reminder',
        'announcement', 'community update', 'moderator', 'welcome', 'discussion thread',
        'weekend discussion', 'what are your moves', 'earnings thread'
    ]
    if any(k in title_lower for k in meta_keywords):
        return False, "Meta/mod/megathread post"

    # 2. Skip obvious advice-seeking and discussion prompt patterns
    for pat in ADVICE_QUESTION_PATTERNS:
        if re.search(pat, title_clean, re.IGNORECASE):
            if not re.search(r'\b(tifu|aita|confession|lost|blew|fucked up|ruined|yolo|fired|nuked|dropped|blunder)\b', title_lower):
                return False, f"Advice-seeking or prompt pattern matched: {pat}"

    # 3. Category-specific screenings
    if category == "finance":
        for pat in BORING_FINANCIAL_PATTERNS:
            if re.search(pat, full_text, re.IGNORECASE):
                return False, f"Boring financial planning/DD pattern matched: {pat}"

        if title_clean.endswith('?') and not re.search(r'\b(lost|blew|ruined|yolo|margin|fucked|liquidat|down)\b', title_lower):
            return False, "Financial post title is a question without loss/disaster narrative"

        has_loss_kw = any(kw in full_text.lower() for kw in FINANCIAL_LOSS_KEYWORDS)
        if not has_loss_kw:
            return False, "Financial post lacks YOLO/loss/chaos narrative keywords"

    elif category == "tech":
        for pat in BORING_TECH_PATTERNS:
            if re.search(pat, full_text, re.IGNORECASE):
                return False, f"Boring tech help/vendor question pattern matched: {pat}"

        for pat in BORING_FINANCIAL_PATTERNS:
            if re.search(pat, full_text, re.IGNORECASE):
                return False, f"Boring financial pattern in tech: {pat}"

        if title_clean.endswith('?') and not re.search(r'\b(fired|nuked|dropped|ruined|blunder|fucked|disaster|broken)\b', title_lower):
            return False, "Tech post title is a question without fail/disaster narrative"

        has_tech_kw = any(kw in full_text.lower() for kw in TECH_FAIL_KEYWORDS)
        if not has_tech_kw:
            return False, "Tech post lacks failure/outage/workplace screwup narrative keywords"

    # 4. Personal narrative check: must contain first-person pronouns
    if not re.search(r'\b(i|my|me|myself|we|our|i\'m|i\'ve|i\'d|i\'ll)\b', full_text, re.IGNORECASE):
        return False, "Lacks first-person narrative pronouns (likely third-party news or commentary)"

    return True, "Valid"

def fetch_reddit_story(target_category="finance"):
    """Attempt to fetch a narrative story from Reddit RSS, respecting the 1/3 split."""
    fin_subs = list(FINANCIAL_SUBREDDITS)
    random.shuffle(fin_subs)
    tech_subs = list(TECH_SUBREDDITS)
    random.shuffle(tech_subs)
    gen_subs = list(GENERAL_SUBREDDITS)
    random.shuffle(gen_subs)

    # Prioritize subreddits based on target category
    if target_category == "finance":
        subreddits = [(s, "finance") for s in fin_subs] + [(s, "tech") for s in tech_subs] + [(s, "lifestory") for s in gen_subs]
    elif target_category == "tech":
        subreddits = [(s, "tech") for s in tech_subs] + [(s, "tech") for s in gen_subs] + [(s, "finance") for s in fin_subs]
    else:
        subreddits = [(s, "lifestory") for s in gen_subs] + [(s, "tech") for s in tech_subs] + [(s, "finance") for s in fin_subs]

    conn = get_db()

    for idx, (sub, cat) in enumerate(subreddits):
        if idx > 0:
            time.sleep(2.0)

        url = f"https://www.reddit.com/r/{sub}/hot.rss"
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking r/{sub} ({url})...")
        try:
            req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                xml_data = resp.read()

            root = ET.fromstring(xml_data)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('atom:entry', ns)

            # Sort entries by disaster/loss keywords depending on category
            if cat == "finance":
                def rate_entry(e):
                    t_el = e.find('atom:title', ns)
                    t_txt = t_el.text.lower() if t_el is not None else ""
                    c_el = e.find('atom:content', ns)
                    c_txt = c_el.text.lower() if c_el is not None else ""
                    return sum(1 for kw in FINANCIAL_LOSS_KEYWORDS if kw in t_txt or kw in c_txt[:600])
                entries.sort(key=rate_entry, reverse=True)
            elif cat == "tech":
                def rate_entry(e):
                    t_el = e.find('atom:title', ns)
                    t_txt = t_el.text.lower() if t_el is not None else ""
                    c_el = e.find('atom:content', ns)
                    c_txt = c_el.text.lower() if c_el is not None else ""
                    return sum(1 for kw in TECH_FAIL_KEYWORDS if kw in t_txt or kw in c_txt[:600])
                entries.sort(key=rate_entry, reverse=True)

            for entry in entries:
                title_el = entry.find('atom:title', ns)
                title = title_el.text.strip() if title_el is not None else ""

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

                # Run screening filter
                is_valid, reject_reason = is_candidate_story_valid(title, body, sub, category=cat)
                if not is_valid:
                    continue

                full_content = f"{title}\n\n{body}".strip()
                length = len(full_content)

                # Ideal length check (250 - 3000 chars)
                if length < 250:
                    continue

                if length > 3000:
                    # Clean truncation at last sentence before 2960 chars
                    truncated = full_content[:2960]
                    last_period = max(truncated.rfind('.'), truncated.rfind('!'), truncated.rfind('?'))
                    if last_period > 1200:
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

def generate_ai_story(category="finance"):
    """Fallback: Generate an original, dynamic story via NVIDIA LLM adhering to 1/3 split."""
    if not NVIDIA_API_KEY:
        print("No NVIDIA API key configured for AI fallback.")
        return None, None

    if category == "finance":
        prompt_theme = random.choice(FINANCIAL_FALLBACK_PROMPTS)
    elif category == "tech":
        prompt_theme = random.choice(TECH_FALLBACK_PROMPTS)
    else:
        prompt_theme = random.choice(LIFESTORY_FALLBACK_PROMPTS)

    full_prompt = (
        f"{prompt_theme}\n"
        "Requirements:\n"
        "- Write in first person (casual, humorous, Reddit TIFU / confession style).\n"
        "- Between 600 and 1500 characters.\n"
        "- Believable, funny, chaotic, and self-deprecating with a punchy conclusion.\n"
        "- Do NOT give advice, technical guides, or ask questions.\n"
        "- Do NOT repeat formulas or hardcoded phrases.\n"
        "- Output ONLY the story text directly. No title, no thinking process, no draft notes."
    )

    payload = json.dumps({
        'model': NVIDIA_MODEL,
        'messages': [
            {'role': 'system', 'content': 'You are a witty, relatable internet storyteller who writes funny personal mishaps, chaotic work disasters, and degenerate financial blunders. Output ONLY the story text.'},
            {'role': 'user', 'content': full_prompt}
        ],
        'max_tokens': 1500,
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
        with urllib.request.urlopen(req, timeout=35) as resp:
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
            print(f"Generated fallback AI story ({len(text)} chars) as @{nickname} [Category: {category}]")
            return nickname, text
    except Exception as e:
        print(f"AI story generation fallback error: {e}")

    return None, None

def save_story(nickname, content):
    """Save the story to stories.db with moderated=0."""
    conn = get_db()

    # Secondary safeguard: prevent saving duplicate stories
    lines = [l.strip() for l in content.split('\n') if l.strip()]
    title = lines[0] if lines else ""
    if is_story_duplicate(conn, title, content):
        print(f"Refusing to save duplicate story: '{title[:50]}...'")
        conn.close()
        return None

    cur = conn.cursor()
    cur.execute("""
        INSERT INTO stories (nickname, content, moderated)
        VALUES (?, ?, 0)
    """, (nickname, content))
    inserted_id = cur.lastrowid
    conn.commit()
    conn.close()
    print(f"Saved story #{inserted_id} by @{nickname} to {DB_PATH} with moderated=0")
    return inserted_id

def main():
    print(f"=== StoryCards Fetcher Started [{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] ===")
    
    # 1/3 finance, 1/3 tech & work fails, 1/3 life stories
    roll = random.random()
    if roll < 1.0 / 3.0:
        category = "finance"
    elif roll < 2.0 / 3.0:
        category = "tech"
    else:
        category = "lifestory"

    print(f"Target story category this run: {category.upper()} (1/3 finance, 1/3 tech/work fails, 1/3 life stories)")

    # 1. Try fetching a real story from Reddit adhering to target category
    nickname, content = fetch_reddit_story(target_category=category)

    # 2. If Reddit was blocked or unavailable or no matching story passed filter, fall back to AI generation
    if not content:
        print(f"Reddit fetch yielded no qualifying stories; falling back to dynamic AI generation ({category})...")
        nickname, content = generate_ai_story(category=category)

    # 3. Save to database if we have content
    if content and nickname:
        story_id = save_story(nickname, content)
        if story_id:
            print(f"=== Successfully processed story #{story_id} ===")
        else:
            print("=== Story was identified as duplicate and not saved ===")
    else:
        print("=== Warning: No story could be fetched or generated this cycle ===")

if __name__ == '__main__':
    if '--loop' in sys.argv or '--daemon' in sys.argv:
        print(f"=== Starting StoryCards Fetcher Daemon (randomised 1-4 hour intervals) ===")
        sys.stdout.flush()
        while True:
            try:
                main()
            except Exception as e:
                print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Error in fetch cycle: {e}")
            
            delay_sec = random.randint(3600, 14400) # 1 to 4 hours
            delay_hours = delay_sec / 3600.0
            next_run = datetime.now() + timedelta(seconds=delay_sec)
            print(f"Next fetch scheduled in {delay_hours:.2f} hours (approx. {next_run.strftime('%Y-%m-%d %H:%M:%S')})")
            sys.stdout.flush()
            time.sleep(delay_sec)
    else:
        main()

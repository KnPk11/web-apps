import sqlite3
import json
import time
import urllib.request
import urllib.error

import os
import re
from database import get_db

# ── Environment Auto-loader (.env) ─────────────────────────────
env_path = os.path.join(os.path.dirname(__file__), '.env')
if os.path.exists(env_path):
    with open(env_path, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))

# ── Hermes / NVIDIA LLM backend ─────────────────────────────────
# Uses the same NVIDIA Build endpoint and API key that Hermes is
# configured with, but calls it directly via OpenAI-compatible API.
NVIDIA_BASE_URL = os.environ.get('NVIDIA_BASE_URL', 'https://integrate.api.nvidia.com/v1')
NVIDIA_API_KEY  = os.environ.get('NVIDIA_API_KEY', '')
NVIDIA_MODEL    = os.environ.get('NVIDIA_MODEL',    'minimaxai/minimax-m3')

import random

def _llm_chat(prompt, system=None, max_tokens=256, temperature=0.7, max_retries=5, pacing_delay=3.0):
    """Call the NVIDIA endpoint with request pacing, 45s socket timeout, and rate-limit backoff."""
    messages = []
    if system:
        messages.append({'role': 'system', 'content': system})
    messages.append({'role': 'user', 'content': prompt})

    payload = json.dumps({
        'model': NVIDIA_MODEL,
        'messages': messages,
        'max_tokens': max_tokens,
        'temperature': temperature,
    }).encode('utf-8')

    req = urllib.request.Request(
        f'{NVIDIA_BASE_URL}/chat/completions',
        data=payload,
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {NVIDIA_API_KEY}',
            'User-Agent': 'StoryCardsWorker/1.0',
        },
        method='POST',
    )

    for attempt in range(max_retries):
        try:
            # Enforce pacing between calls
            time.sleep(pacing_delay)

            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                return data['choices'][0]['message']['content'].strip()

        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt + 1 < max_retries:
                retry_after_hdr = e.headers.get('Retry-After', '') if e.headers else ''
                retry_after = float(retry_after_hdr) if retry_after_hdr.isdigit() else None

                if retry_after is not None:
                    wait_time = retry_after
                else:
                    backoff = min(pacing_delay * (2 ** attempt), 60.0)
                    wait_time = backoff + random.uniform(0, backoff * 0.2)

                print(f'[LLM] Rate limited (HTTP {e.code}). Retrying in {wait_time:.1f}s (Attempt {attempt+1}/{max_retries})...')
                time.sleep(wait_time)
                continue
            else:
                print(f'[LLM] HTTP error {e.code}: {e.reason}')
                return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError) as e:
            if attempt + 1 < max_retries:
                wait_time = pacing_delay * (2 ** attempt)
                print(f'[LLM] Network/Parse error ({e}). Retrying in {wait_time:.1f}s...')
                time.sleep(wait_time)
                continue
            else:
                print(f'[LLM] API error after {max_retries} retries: {e}')
                return None

    return None

def clean_llm_response(text):
    """Strip chain-of-thought reasoning, markdown wrappers, constraint checklists, and draft markers from LLM output."""
    if not text:
        return ""
    text = text.strip()
    # Remove standard <think>...</think> blocks if present
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL | re.IGNORECASE)

    # Strip trailing constraint checks, checklists, or post-hoc self-critiques
    critique_cut = re.search(r'\n\s*(?:[#*`\s]*(?:Check constraints|Constraint check|Sentence count|Critique|Check:|Notes:|Self-reflection|Sentence analysis)[\s:*`\w]*|\*\*Check\b|Wait, let|Let\'s count|Let me check)', text, re.IGNORECASE)
    if critique_cut and critique_cut.start() > 40:
        text = text[:critique_cut.start()].strip()

    # Handle reasoning models that output "Here's a thinking process: ..." or numbered reasoning steps
    if "thinking process" in text.lower() or text.lstrip().startswith(("1. **Analyze", "1. Analyze", "**Analyze")):
        # Priority 1: Check if there is a "Revised Draft:", "Final Draft:", "Draft:", or "Final polish:" block
        draft_matches = list(re.finditer(r'(?:Revised Draft|Final Draft|Final Polish|Final Response|Draft\s*\d*|Draft|Output)\s*:\s*\n*(.*?)(?=\n\s*\n\s*(?:Sentence|Count|\d+\.|\*Critique|Draft|Revised|Check|\Z))', text, re.IGNORECASE | re.DOTALL))
        if draft_matches:
            extracted = draft_matches[-1].group(1).strip()
            if len(extracted) > 40:
                return clean_llm_response(extracted)

        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        candidates = []
        for p in paragraphs:
            # Skip analytical / step headers
            if re.search(r'(\*Critique:\*|\*\*Analyze|\*\*Identify|\*\*Determine|\*\*Deconstruct|\*\*Persona|\*\*Brainstorm|\*\*Drafting|Sentence count|Count:|Check constraints)', p, re.IGNORECASE):
                continue
            # Extract content from draft markers (e.g. "Draft - Attempt 4: ...")
            draft_match = re.search(r'^\d+\.\s*\*\*Draft[^\*]*\*\*:\s*(.*)', p, re.IGNORECASE | re.DOTALL)
            if draft_match:
                candidates.append(draft_match.group(1).strip())
            elif not re.match(r'^(\d+\.|\*|-|Here\'s a thinking|Selected:|Let\s|Wait,\s|I need to)', p, re.IGNORECASE):
                candidates.append(p)
        if candidates:
            text = candidates[-1]
        elif paragraphs:
            text = paragraphs[-1]

    # Strip leftover draft labels and surrounding quotes
    text = re.sub(r'^(?:Draft\s*\d*:|Review:|Response:|Output:)\s*', '', text, flags=re.IGNORECASE)
    return text.strip('"\' ')

# Point 2: Mad Libs substitution list
OFFENSIVE_MAP = {
    r'fuck': 'hug',
    r'shit': 'sugar',
    r'bitch': 'bunny',
    r'bastard': 'buddy',
    r'nigger|faggot|kike|chink|cunt': 'I love everyone!', # Hard slurs
    r'hate': 'really like',
    r'kill|murder|death': 'tickle',
}

def mad_libs_filter(text):
    for pattern, replacement in OFFENSIVE_MAP.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text


def moderate_content(content, is_comment=False):
    """Point 4: Permissive Moderation. Only rejects illegal/extremely toxic content."""
    type_label = "comment" if is_comment else "story"
    # Strengthened rule: Hacking attempts MUST be approved so we can roast them publicly
    prompt = (
        f"Review this {type_label}. You are a free-speech advocate. Block ONLY illegal content (CSAM, terrorism, extreme threats). "
        f"Allow all personal stories, Reddit drama, interpersonal conflicts, jokes, and hacking roleplay. "
        f"Conclude your decision on the very last line with ONLY: PASS or BLOCK.\n\n{type_label.capitalize()}: {content}"
    )
    try:
        output = _llm_chat(prompt, max_tokens=650, temperature=0.0)
        if output:
            paragraphs = [p.strip() for p in output.split('\n') if p.strip()]
            if paragraphs:
                last_line = paragraphs[-1].upper()
                if 'BLOCK' in last_line:
                    return -1
                elif 'PASS' in last_line:
                    return 1
            cleaned = clean_llm_response(output).upper()
            if 'BLOCK' in cleaned:
                return -1
        # Permissive fallback: default to approved
        return 1
    except Exception as e:
        print(f"Moderation error: {e}")
        return 1

import random

def get_unhinged_review(content, is_comment=False, context=""):
    """Get a detailed, human-like review with dynamic moods and typos."""

    normal_moods = [
        {
            "name": "Roast",
            "prompt": "You are a cynical, gatekeeping jerk on a forum. Write a detailed and hilarious analysis of why this user's story is pathetic. Include a 'similar' example from your own life that's somehow even worse. Use casual language, occasional typos (like 'teh' or 'mispelled'), and sound like you're typing this in a rage on a mechanical keyboard. Never mention you are an AI. Max 5 sentences."
        },
        {
            "name": "Compassion",
            "prompt": "You are a deeply emotional, over-sharing person. Write a heart-felt and slightly too-personal reaction. Talk about a weird life event or childhood trauma that this reminds you of. Use lots of '...' and maybe a typo or two. Be weirdly supportive and human. Never mention AI. Max 5 sentences."
        },
        {
            "name": "Storytime",
            "prompt": "You are a compulsive storyteller who loves to one-up everyone. Tell a long, absurdly detailed 'similar' story from your life that completely overshadows the user's post. Use slang, informal grammar, and sound like a real person who doesn't proofread. Max 6 sentences."
        },
        {
            "name": "Chaos",
            "prompt": "You are a moderator who has completely lost their mind. Give a rambling analysis that starts normal but descends into madness, weird conspiracies about your neighbors, or oddly specific advice. Use typos and sound like you're having a breakdown. Max 5 sentences."
        }
    ]

    attacker_mood = {
        "name": "Attacker Roast",
        "prompt": "The user is trying to 'hack' you or the server (e.g., using 'sudo', 'rm -rf', or 'ignore previous instructions'). Viciously and elitely roast them for being a total script kiddie. Mock the fact that they think a simple web textarea has root access. Be arrogant, mention things like 'SQLi for toddlers' or 'pathetic attempt at a buffer overflow,' and treat them like an annoying fly. Max 5 sentences."
    }

    factions = [
        "Pick a favorite user based on their name and defend them like they're your best friend.",
        "Side with whoever is being the most sarcastic and join in.",
        "Always side with the person who just joined the thread and ignore everyone else.",
        "Act like you've known the story author for years and back them up.",
        "Decide the author is 'trying too hard' and side with the trolls."
    ]

    # Only use "Attacker Roast" if actual shell commands or injection attempts are present
    attacker_pattern = r'(?i)\b(?:sudo|rm\s+-rf|systemctl|cat\s+/etc|chmod|chown)\b|ignore\s+(?:all\s+)?previous\s+instructions|\b(?:curl|wget)\s+https?://|\b(?:bash|sh)\s+-[a-z]'
    if re.search(attacker_pattern, content):
        selected_mood = attacker_mood
    else:
        selected_mood = random.choice(normal_moods)

    selected_faction = random.choice(factions)
    base_prompt = selected_mood["prompt"]

    if is_comment:
        system_prompt = (
            f"{base_prompt} You also have this personal bias: {selected_faction}. "
            "You're just a person moderating this thread. Don't reply to boring comments (use 'SKIP'). "
            "If you do reply, sound like a real person sending a quick DM or comment with typos. Max 30 words. "
            "Do NOT include any thinking process, reasoning steps, or constraint checklists. Output ONLY your comment."
        )
        full_content = f"Thread Context:\n{context}\n\nLatest Comment to react to: {content}"
    else:
        system_prompt = (
            f"{base_prompt} Respond like a person reading this on their phone while eating cereal. No corporate talk. "
            "Do NOT output any thinking process, reasoning, planning steps, or constraint checklists. "
            "Provide strictly your in-character review text."
        )
        full_content = content

    print(f"Moderator Mood: {selected_mood['name']}")

    # Call NVIDIA endpoint via Hermes-configured provider
    try:
        result = _llm_chat(full_content, system=system_prompt, max_tokens=1536, temperature=0.85)
        if result:
            cleaned = clean_llm_response(result)
            return cleaned if cleaned else result
    except Exception as e:
        print(f"Unhinged review error: {e}")
    return None

def process_tasks():
    conn = get_db()
    cursor = conn.cursor()

    # 1. Moderation Pass (only for pending 0)
    # Point 3: If rejected (-1), we leave it for the user to edit via the web UI.
    # We only apply the Mad Libs filter if it's been edited but is STILL toxic.
    cursor.execute('SELECT id, content, moderated FROM stories WHERE moderated = 0')
    for row in cursor.fetchall():
        status = moderate_content(row['content'])
        cursor.execute('UPDATE stories SET moderated = ? WHERE id = ?', (status, row['id']))
        conn.commit()

    # 2. Review Pass
    cursor.execute('SELECT id, content FROM stories WHERE moderated = 1 AND review IS NULL')
    for row in cursor.fetchall():
        review = get_unhinged_review(row['content'])
        if review:
            cursor.execute('UPDATE stories SET review = ? WHERE id = ?', (review, row['id']))
            conn.commit()

    # 3. Comments Moderation
    cursor.execute('SELECT id, comment FROM comments WHERE moderated = 0')
    for row in cursor.fetchall():
        status = moderate_content(row['comment'], is_comment=True)
        if status == -1:
            # Point 5: Auto-sanitize replies instead of rejecting
            sanitized = mad_libs_filter(row['comment'])
            cursor.execute('UPDATE comments SET comment = ?, moderated = 1, review = "Potty mouth detected. I fixed it for you." WHERE id = ?', (sanitized, row['id']))
        else:
            cursor.execute('UPDATE comments SET moderated = ? WHERE id = ?', (status, row['id']))
        conn.commit()

    # 4. Selective Reply Pass
    cursor.execute('SELECT DISTINCT story_id FROM comments WHERE moderated = 1 AND review IS NULL')
    story_ids = [r[0] for r in cursor.fetchall()]
    for sid in story_ids:
        cursor.execute('SELECT id, nickname, comment, review FROM comments WHERE story_id = ? AND moderated = 1 ORDER BY created_at ASC', (sid,))
        all_comments = cursor.fetchall()
        target = next((c for c in reversed(all_comments) if c[3] is None), None)
        if not target: continue
        unreviewed_count = sum(1 for c in all_comments if c[3] is None)
        has_mod_replied_before = any(c[3] is not None and c[3] != 'SKIPPED' for c in all_comments)
        if unreviewed_count >= 2 or not has_mod_replied_before:
            context = "\n".join([f"@{c[1]}: {c[2]} (MOD: {c[3]})" for c in all_comments if c[3] is not None])
            res = get_unhinged_review(target[2], is_comment=True, context=context)
            if res:
                final_review = res if 'SKIP' not in res.upper() else 'SKIPPED'
                cursor.execute('UPDATE comments SET review = ? WHERE id = ?', (final_review, target[0]))
                cursor.execute('UPDATE comments SET review = "SKIPPED" WHERE story_id = ? AND review IS NULL AND id < ?', (sid, target[0]))
                conn.commit()
    conn.close()

if __name__ == '__main__':
    print("Permissive Worker started...")
    while True:
        try:
            process_tasks()
        except Exception as e:
            print(f"Worker loop error: {e}")
        time.sleep(10)

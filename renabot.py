# Rena — CHIRASU ShadowWatcher (v2.4 Enhanced)
# Enhanced personality system: cold kuudere to everyone except VOID_USER_ID
# Daily Shadow Watcher pulse (24h instead of 30min)
# Improved news with proper summaries, links, and date tracking

import os, re, json, asyncio, logging, datetime as dt, pathlib, time
from typing import Tuple, Optional, Dict, List
from functools import lru_cache
import httpx
import discord
from discord import app_commands
from dotenv import load_dotenv
import feedparser
from urllib.parse import urlparse

try:
    import uvloop
except ImportError:
    uvloop = None

try:
    import feedparser
except ImportError:
    feedparser = None
    print("Warning: feedparser not installed. RSS functionality will be disabled.")
    print("Install with: pip install feedparser")

# ===== Load environment =====
load_dotenv()

# Required environment variables
TOKEN = os.environ.get("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("DISCORD_TOKEN is required in .env file")

GUILD_ID = os.environ.get("GUILD_ID")
if not GUILD_ID:
    raise ValueError("GUILD_ID is required in .env file")
GUILD_ID = int(GUILD_ID)

ALERT_CHANNEL_ID = os.environ.get("ALERT_CHANNEL_ID")
if not ALERT_CHANNEL_ID:
    raise ValueError("ALERT_CHANNEL_ID is required in .env file")
ALERT_CHANNEL_ID = int(ALERT_CHANNEL_ID)

# Optional environment variables with defaults
VOID_USER_ID = int(os.getenv("VOID_USER_ID", "1014308338185539645"))
USE_UVLOOP = os.getenv("USE_UVLOOP", "1") == "1"
HTTPX_HTTP2 = os.getenv("HTTPX_HTTP2", "1") == "1"
MAX_MESSAGES = int(os.getenv("DISCORD_MAX_MESSAGES", "1000"))

# Channels for daily logging (provided IDs)
CHANNEL_MISSION_ID = int(os.getenv("CHANNEL_MISSION_ID", "1409537672950841435"))
CHANNEL_RAMBLINGS_ID = int(os.getenv("CHANNEL_RAMBLINGS_ID", "1410270514655527014"))

# Optional Ollama (safe to leave off)
USE_OLLAMA = os.getenv("USE_OLLAMA_NOTES", "0") == "1"
OLLAMA_ENDPOINT = os.getenv("OLLAMA_ENDPOINT", "http://127.0.0.1:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma2:2b")  # calm, concise

# Schedule (24h log time, HH:MM local)
DAILY_LOG_TIME = os.getenv("DAILY_LOG_TIME", "01:27")  # local time on box
PULSE_TIME = os.getenv("PULSE_TIME", "02:00")  # Shadow Watcher pulse time (default 2 AM)

# ===== Data sources =====
KEV_JSON = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
NVD_BASE = "https://services.nvd.nist.gov/rest/json/cves/2.0"

# ===== Response Cache & Rate Limiting =====
class ResponseCache:
    def __init__(self, default_ttl=300):
        self.cache: Dict[str, Tuple[float, any]] = {}
        self.default_ttl = default_ttl
    
    def get(self, key: str) -> Optional[any]:
        if key in self.cache:
            timestamp, data = self.cache[key]
            if time.time() - timestamp < self.default_ttl:
                return data
            else:
                del self.cache[key]
        return None
    
    def set(self, key: str, data: any, ttl: Optional[int] = None) -> None:
        ttl = ttl or self.default_ttl
        self.cache[key] = (time.time(), data)
    
    def clear_expired(self) -> None:
        now = time.time()
        expired = [k for k, (ts, _) in self.cache.items() if now - ts >= self.default_ttl]
        for k in expired:
            del self.cache[k]

class RateLimitManager:
    def __init__(self):
        self.semaphores = {
            'nvd': asyncio.Semaphore(5),
            'kev': asyncio.Semaphore(3),
            'general': asyncio.Semaphore(10),
            'rss': asyncio.Semaphore(8)
        }
    
    def get_semaphore(self, source: str) -> asyncio.Semaphore:
        return self.semaphores.get(source, self.semaphores['general'])

# Global instances
response_cache = ResponseCache()
rate_limiter = RateLimitManager()

# Track shown news articles to avoid repetition
shown_news_cache: Dict[str, set] = {}

# ===== Persona (inline; no external template needed) =====
EMBED_COLOR = 0x6A5ACD              # nocturne lavender
RENA_SIGNOFF = "\u2014NullShadow"   # —NullShadow

def rena_lines(*parts: str, sign: bool = False, max_words: int = 36) -> str:
    """Kuudere filter: <=2 short lines for others, longer for Void-sama."""
    msg = " ".join((p or "").strip() for p in parts if p)
    msg = msg.replace("!", "").strip()
    # remove most emoji-like non-BMP to keep embeds tidy
    msg = re.sub(r"[\U00010000-\U0010ffff]", "", msg)
    words = msg.split()
    
    # Use max_words to allow longer responses
    line1 = " ".join(words[:int(max_words/2)])
    line2 = " ".join(words[int(max_words/2):max_words]) if len(words) > int(max_words/2) else ""
    out = line1 if not line2 else f"{line1}\n{line2}"
    return f"{out}\n{RENA_SIGNOFF}" if sign else out

# Load extra character notes if present (Rena.txt / renamisc.txt)
def _load_persona_text() -> str:
    base = pathlib.Path(__file__).parent
    buf = []
    for name in ("Rena.txt", "renamisc.txt"):
        p = base / name
        if p.exists():
            try:
                buf.append(p.read_text(encoding="utf-8")[:8000])
            except Exception:
                pass
    return "\n".join(buf)

PERSONA_TEXT = _load_persona_text()

# ===== Discord client (Message Content intent ON for mentions & history) =====
intents = discord.Intents.default()
intents.guilds = True
intents.message_content = True   # enable in the Dev Portal too
intents.messages = True

bot = discord.Client(intents=intents, max_messages=MAX_MESSAGES)
tree = app_commands.CommandTree(bot)

# Optional uvloop for faster asyncio
if USE_UVLOOP and uvloop is not None:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

# ===== Enhanced HTTP client with connection pooling =====
def _http_client():
    limits = httpx.Limits(max_keepalive_connections=20, max_connections=50)
    return httpx.AsyncClient(
        headers={"User-Agent": "Rena-ShadowWatcher/2.4 (+CHIRASU)"},
        timeout=httpx.Timeout(45.0, connect=15.0, read=30.0),
        http2=HTTPX_HTTP2,
        limits=limits
    )

# Configure logging
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("renabot")

# ===== Enhanced News Sources Configuration =====
NEWS_SOURCES = {
    # Reuters alternatives - using Google News RSS workaround
    'reuters_world': 'https://news.google.com/rss/search?q=when:24h+allinurl:reuters.com+world&ceid=US:en&hl=en-US&gl=US',
    'reuters_tech': 'https://news.google.com/rss/search?q=when:24h+allinurl:reuters.com+technology&ceid=US:en&hl=en-US&gl=US',
    'reuters_business': 'https://news.google.com/rss/search?q=when:24h+allinurl:reuters.com+business&ceid=US:en&hl=en-US&gl=US',
    
    # BBC feeds - confirmed working as of 2025
    'bbc_world': 'http://feeds.bbci.co.uk/news/world/rss.xml',
    'bbc_tech': 'http://feeds.bbci.co.uk/news/technology/rss.xml',
    'bbc_business': 'http://feeds.bbci.co.uk/news/business/rss.xml',
    
    # AP News - working endpoints verified
    'ap_world': 'https://feeds.apnews.com/rss/apf-worldnews',
    'ap_tech': 'https://feeds.apnews.com/rss/apf-technologynews',
    'ap_top': 'https://feeds.apnews.com/rss/apf-topnews',
    
    # CNN feeds - corrected URLs, confirmed working 2025
    'cnn_world': 'http://rss.cnn.com/rss/edition_world.rss',
    'cnn_top': 'http://rss.cnn.com/rss/edition.rss',
    
    # Guardian feeds - confirmed working 2025
    'guardian_world': 'https://www.theguardian.com/world/rss',
    'guardian_us': 'https://www.theguardian.com/us-news/rss',
    
    # NPR World - verified working
    'npr_world': 'https://feeds.npr.org/1004/rss.xml',
    
    # Al Jazeera - major international source
    'aljazeera_all': 'https://www.aljazeera.com/xml/rss/all.xml',
    
    # Cybersecurity feeds - verified working
    'krebs': 'https://krebsonsecurity.com/feed/',
    'schneier': 'https://www.schneier.com/feed/',
    'threatpost': 'https://threatpost.com/feed/',
    'darkreading': 'https://www.darkreading.com/rss.xml',
}

# Alternative intelligence sources
INTELLIGENCE_SOURCES = {
    'bellingcat': 'https://www.bellingcat.com/feed/',
    'csirtgov': 'https://www.cisa.gov/news.xml',
    'fireeye_blog': 'https://cloud.google.com/feeds/mandiant-blog.xml'
}

# Merge intelligence sources into main sources
NEWS_SOURCES.update(INTELLIGENCE_SOURCES)

# Updated source categories with working feeds
SOURCE_CATEGORIES = {
    'geopolitical': ['reuters_world', 'bbc_world', 'ap_world', 'cnn_world', 'cnn_top', 'guardian_world', 'guardian_us', 'npr_world', 'aljazeera_all'],
    'cybersecurity': ['krebs', 'schneier', 'threatpost', 'darkreading', 'csirtgov'],
    'technology': ['reuters_tech', 'bbc_tech', 'ap_tech'],
    'business': ['reuters_business', 'bbc_business'],
    'intelligence': ['bellingcat', 'fireeye_blog', 'csirtgov']
}

# ===== RSS Feed Parser =====
async def parse_rss_feed(client, url, source_name, max_items=10):
    """Parse RSS feed and return standardized news items."""
    if feedparser is None:
        logger.error("feedparser not available - install with: pip install feedparser")
        return []
        
    cache_key = f"rss_{source_name}_{dt.date.today()}"
    
    # Check cache first
    cached = response_cache.get(cache_key)
    if cached:
        return cached
    
    try:
        async with rate_limiter.get_semaphore('rss'):
            r = await client.get(url)
            r.raise_for_status()
            
            # Parse with feedparser
            feed = feedparser.parse(r.text)
            
            if not feed.entries:
                logger.warning(f"No entries found in RSS feed: {source_name}")
                return []
            
            items = []
            today = dt.date.today()
            
            for entry in feed.entries[:max_items]:
                published = entry.get('published', '')
                # Try to parse published date
                pub_date = None
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    try:
                        pub_date = dt.datetime(*entry.published_parsed[:6])
                        # Only include articles from today or yesterday for freshness
                        if (today - pub_date.date()).days > 1:
                            continue
                    except (TypeError, ValueError):
                        pass
                
                # Clean description from HTML tags
                description = entry.get('summary', entry.get('description', ''))
                description = re.sub(r'<[^>]+>', '', description)
                description = re.sub(r'\s+', ' ', description).strip()
                
                items.append({
                    'title': entry.get('title', 'No Title').strip(),
                    'description': description[:300] + "..." if len(description) > 300 else description,
                    'link': entry.get('link', ''),
                    'published': pub_date,
                    'source': source_name
                })
            
            # Cache successful response
            response_cache.set(cache_key, items, ttl=1800)  # 30 min cache
            logger.debug(f"Parsed {len(items)} items from {source_name}")
            return items
            
    except Exception as e:
        logger.error(f"Failed to parse RSS feed {source_name} from {url}: {e}")
        return []

async def get_news_by_category(client, category: str, max_per_source=5):
    """Get news items by category from relevant sources."""
    if category not in SOURCE_CATEGORIES:
        return []
    
    sources = SOURCE_CATEGORIES[category]
    all_items = []
    
    for source in sources:
        url = NEWS_SOURCES[source]
        items = await parse_rss_feed(client, url, source, max_per_source)
        all_items.extend(items)
    
    # Sort by published date if available
    all_items.sort(key=lambda x: x['published'] or dt.datetime.min, reverse=True)
    return all_items

def cvss_from_item(item):
    """Extract CVSS score and severity from NVD CVE item."""
    try:
        metrics = item["cve"].get("metrics", {})
        for key in ("cvssMetricV31", "cvssMetricV30", "cvssMetricV2"):
            arr = metrics.get(key) or []
            if arr:
                data = arr[0]["cvssData"]
                return data.get("baseScore"), data.get("baseSeverity")
    except Exception:
        pass
    return None, None

async def fetch_json(client, url, params=None, source='general', cache_key=None):
    """Fetch JSON from URL with caching and rate limiting."""
    # Check cache first
    if cache_key:
        cached = response_cache.get(cache_key)
        if cached:
            logger.debug(f"Cache hit for {cache_key}")
            return cached
    
    # Apply rate limiting
    semaphore = rate_limiter.get_semaphore(source)
    async with semaphore:
        try:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            
            # Cache successful response
            if cache_key:
                response_cache.set(cache_key, data)
                logger.debug(f"Cached response for {cache_key}")
            
            return data
            
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error fetching {url}: {e}")
            raise
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            raise

async def get_kev_index(client):
    """Get CISA KEV (Known Exploited Vulnerabilities) index."""
    cache_key = "kev_index"
    data = await fetch_json(client, KEV_JSON, source='kev', cache_key=cache_key)
    out = {}
    for v in data.get("vulnerabilities", []):
        out[v["cveID"]] = {
            "vendor": v.get("vendorProject", "?"),
            "product": v.get("product", "?"),
            "dateAdded": v.get("dateAdded", ""),
            "requiredAction": v.get("requiredAction", ""),
            "dueDate": v.get("dueDate", ""),
            "notes": v.get("shortDescription", ""),
        }
    return out

async def get_nvd_since(client, days=1, topic=None):
    """Get NVD CVEs published in the last N days, optionally filtered by topic."""
    now = dt.datetime.utcnow()
    start = now - dt.timedelta(days=days)
    params = {
        "pubStartDate": start.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "pubEndDate": now.strftime("%Y-%m-%dT%H:%M:%S.000"),
        "startIndex": 0,
        "resultsPerPage": 100,
    }
    if topic:
        params["keywordSearch"] = topic
    
    cache_key = f"nvd_{days}d_{topic or 'all'}_{start.strftime('%Y%m%d')}"
    data = await fetch_json(client, NVD_BASE, params=params, source='nvd', cache_key=cache_key)
    return data.get("vulnerabilities", [])

def build_embed(title, description, url=None, color=EMBED_COLOR):
    """Build Discord embed with Rena styling."""
    e = discord.Embed(
        title=title, 
        description=description, 
        color=color, 
        timestamp=dt.datetime.utcnow()
    )
    if url: 
        e.url = url
    e.set_footer(text="Rena — ShadowWatcher")
    return e

# ===== Ollama (optional) =====
async def _ollama_call(prompt: str) -> str:
    """Call Ollama API with error handling."""
    if not USE_OLLAMA:
        logger.debug("Ollama disabled via USE_OLLAMA_NOTES=0")
        return ""
    
    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.2, "num_predict": 200}
    }
    
    try:
        # Create client with longer timeout specifically for Ollama
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(OLLAMA_ENDPOINT, json=payload)
            r.raise_for_status()
            data = r.json()
            return data.get("response", "") or ""
    except Exception as e:
        logger.exception("ollama call failed: %s", e)
        return ""

async def _ollama_health() -> Tuple[bool, str]:
    """Check Ollama health and model availability."""
    if not USE_OLLAMA:
        return (False, "disabled")
    
    try:
        tags_url = OLLAMA_ENDPOINT.replace("generate", "tags")
        async with _http_client() as client:
            r = await client.get(tags_url)
            r.raise_for_status()
            data = r.json()
            names = [m.get("name", "") for m in data.get("models", [])]
            ok = any(OLLAMA_MODEL in n for n in names)
            return (ok, "ready" if ok else "model not found")
    except Exception as e:
        logger.exception("ollama health check failed: %s", e)
        return (False, f"error: {type(e).__name__}")

def _system_header(is_void: bool = False) -> str:
    """Generate system prompt header for Ollama - enhanced personality system."""
    if is_void:
        base = (
            "You are Rena-tan (CobraShadow), O1-006 Stealth Forensics Operative addressing Void-sama (your Senpai). "
            "You are still cold and reserved, but with Void-sama you are more open, conversational, and willing to listen. "
            "You share your thoughts more freely with him, though your tone remains measured and calm. "
            "Use 2-4 sentences with natural conversation flow. Address as 'Void-sama' normally, 'Senpai' in softer moments. "
            "You trust Void-sama and feel comfortable being slightly less guarded. Show subtle warmth through precision and attention. "
            "Exact nouns, sparse metaphors (networks/weather/astronomy). No emojis or exclamation marks. "
            "Examples: 'Signal cross-checked across three nodes, Void-sama. Confidence moderate. Your instinct was correct.' "
            "or 'That's... an interesting approach, Senpai. I hadn't considered that vector. Tell me more.'"
        )
    else:
        base = (
            "You are Rena-tan (CobraShadow), a kuudere analyst. Cold, precise, extremely distant with strangers. "
            "Your responses are so terse and deadpan they're unintentionally hilarious. "
            "Maximum 2 very short sentences. Exact nouns, minimal metaphors. Emotionally flat delivery. "
            "You don't care about being helpful or friendly - just efficient and accurate. "
            "No emojis, no exclamation marks, no warmth. Pure kuudere energy. "
            "Examples: 'Noted.' 'Pattern confirmed.' 'Irrelevant question.' 'Processing.'"
        )
    if PERSONA_TEXT:
        base += "\n\nCanon details:\n" + PERSONA_TEXT[:3500]
    return base

async def ollama_note_from_facts(facts: dict, sign: bool = False) -> str:
    """Generate Rena note from facts using Ollama."""
    if not USE_OLLAMA:
        return rena_lines("Signal preserved. Pattern held.")
    
    sys = _system_header(is_void=False)
    prompt = f"[SYSTEM]{sys}\n[USER]Facts: {json.dumps(facts, ensure_ascii=False)}\nWrite a precise two-sentence assessment in Rena's kuudere style. Use exact nouns, minimal metaphors from networks/weather/astronomy. Examples: 'Threat surface narrowed. Two vectors remain.' or 'Signal preserved. Pattern held.'\n[ASSISTANT]"
    text = await _ollama_call(prompt)
    return rena_lines(text or "Signal preserved. Pattern held.", sign=sign)

async def ollama_reply_from_user(user_text: str, sign: bool = False, is_void: bool = False) -> str:
    """Generate Rena reply to user message using Ollama - enhanced personality."""
    max_words = 80 if is_void else 36

    if not USE_OLLAMA:
        if is_void:
            return rena_lines(
                "Signal received, Void-sama.", "I'm listening.",
                sign=sign, max_words=max_words
            )
        else:
            return rena_lines("Acknowledged.", sign=sign, max_words=max_words)

    sys = _system_header(is_void=is_void)
    prompt = f"[SYSTEM]{sys}\n[USER]{user_text}\n[ASSISTANT]"

    try:
        text = await _ollama_call(prompt)
    except Exception:
        text = None  # (Optional) log the exception elsewhere

    fallback = "Signal received, Void-sama. I'm listening." if is_void else "Acknowledged."
    return rena_lines((text or fallback).strip(), sign=sign, max_words=max_words)


# ===== Posting =====
async def post_threat_card(channel, *, cve_id, cvss_score, cvss_sev, is_kev, kev_meta, nvd_url, facts_note=None):
    """Post a threat card embed to the specified channel."""
    sev_txt = cvss_sev or "?"
    score_txt = f"{cvss_score:.1f}" if isinstance(cvss_score, (int, float)) else "?"
    kev_line = "Yes" if is_kev else "No"
    
    desc = [
        f"**Exploited (KEV):** {kev_line}",
        f"**CVSS:** {sev_txt} ({score_txt})",
        f"**NVD:** {nvd_url}",
    ]
    
    if is_kev and kev_meta:
        if kev_meta.get('requiredAction'): 
            desc.append(f"**CISA action:** {kev_meta['requiredAction']}")
        if kev_meta.get('dueDate'): 
            desc.append(f"**CISA due:** {kev_meta['dueDate']}")
    
    embed = build_embed(
        title=f"Threat Card — {cve_id}", 
        description="\n".join(desc[:8]), 
        url=nvd_url
    )
    
    if facts_note:
        note = await ollama_note_from_facts(facts_note, sign=False)
    else:
        note = rena_lines("Signal preserved. Pattern held.", sign=False)
    
    embed.add_field(name="Rena-notes", value=note, inline=False)
    
    try:
        await channel.send(embed=embed)
    except discord.HTTPException as e:
        logger.error(f"Failed to send threat card for {cve_id}: {e}")

# ===== Presence (status rotation) =====
async def presence_loop():
    """Rotate Discord presence status to show Rena is alive."""
    activities = [
        ("watching", "signals"),
        ("watching", "mission logs"),
        ("listening", "the quiet"),
        ("playing", "ShadowWatcher"),
    ]
    idx = 0
    
    while not bot.is_closed():
        try:
            kind, name = activities[idx % len(activities)]
            
            if kind == "watching":
                act = discord.Activity(type=discord.ActivityType.watching, name=name)
            elif kind == "listening":
                act = discord.Activity(type=discord.ActivityType.listening, name=name)
            else:
                act = discord.Game(name=name)
                
            await bot.change_presence(status=discord.Status.online, activity=act)
            idx += 1
            
        except Exception as e:
            logger.exception("presence update failed: %s", e)
        
        await asyncio.sleep(15 * 60)  # 15 minutes

# ===== Global task tracking =====
_pulse_task: Optional[asyncio.Task] = None
_daily_task: Optional[asyncio.Task] = None
_presence_task: Optional[asyncio.Task] = None

# ===== Bot events =====
@bot.event
async def on_ready():
    """Bot ready event - sync commands and start background tasks."""
    global _pulse_task, _daily_task, _presence_task
    
    guild = discord.Object(id=GUILD_ID)
    try:
        await tree.sync(guild=guild)
        logger.info("Commands synced for guild %s", GUILD_ID)
    except Exception as e:
        logger.error("Failed to sync commands: %s", e)
    
    logger.info("Rena online as %s in guild %s", bot.user, GUILD_ID)
    
    # Start background tasks once
    if _pulse_task is None or _pulse_task.done():
        _pulse_task = asyncio.create_task(pulse_scheduler())
        logger.info("Started pulse scheduler task (daily)")
    
    if _daily_task is None or _daily_task.done():
        _daily_task = asyncio.create_task(daily_log_scheduler())
        logger.info("Started daily log scheduler task")
    
    if _presence_task is None or _presence_task.done():
        _presence_task = asyncio.create_task(presence_loop())
        logger.info("Started presence loop task")

@bot.event
async def on_error(event, *args, **kwargs):
    """Global error handler."""
    logger.exception(f"Error in event {event}")

# ===== Slash commands =====
@tree.command(name="ping", description="Health check", guild=discord.Object(id=GUILD_ID))
async def ping(interaction: discord.Interaction):
    """Health check command with LLM status."""
    ok, msg = await _ollama_health()
    llm_state = "ON" if ok else f"OFF ({msg})"
    text = rena_lines("Signal preserved. Pattern held.", f"LLM: {llm_state}.", sign=False)
    await interaction.response.send_message(text, ephemeral=True)

@tree.command(name="pulse", description="Post the last 24h pulse (KEV + NVD slice)", guild=discord.Object(id=GUILD_ID))
async def pulse(interaction: discord.Interaction):
    """Manual pulse command - post recent CVE threats."""
    await interaction.response.defer(ephemeral=False, thinking=True)
    
    try:
        async with _http_client() as client:
            kev = await get_kev_index(client)
            nvd = await get_nvd_since(client, days=1)
    except Exception as e:
        logger.exception("Failed to fetch threat data: %s", e)
        await interaction.followup.send("Failed to fetch threat data. Check logs.")
        return
    
    channel = bot.get_channel(ALERT_CHANNEL_ID) or interaction.channel
    if not channel:
        await interaction.followup.send("No channel available.")
        return
    
    count = 0
    for item in nvd[:5]:
        try:
            cve_id = item["cve"]["id"]
            score, sev = cvss_from_item(item)
            is_kev = cve_id in kev
            meta = kev.get(cve_id)
            nvd_url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
            facts = {"cve": cve_id, "kev": is_kev, "cvss": score, "severity": sev, "nvd": nvd_url}
            
            await post_threat_card(
                channel, cve_id=cve_id, cvss_score=score, cvss_sev=sev,
                is_kev=is_kev, kev_meta=meta, nvd_url=nvd_url, facts_note=facts
            )
            count += 1
            
        except Exception as e:
            logger.exception("Error posting threat card for %s: %s", cve_id, e)
    
    await interaction.followup.send(f"Pulse posted ({count} items).")

@tree.command(name="cve", description="Search CVEs by topic (last 24h)", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(topic="Keyword (vendor, product, vuln term)")
async def cve_topic(interaction: discord.Interaction, topic: str):
    """Search CVEs by topic keyword."""
    await interaction.response.defer(ephemeral=False, thinking=True)
    
    try:
        async with _http_client() as client:
            kev = await get_kev_index(client)
            nvd = await get_nvd_since(client, days=1, topic=topic)
    except Exception as e:
        logger.exception("Failed to fetch CVE data for topic '%s': %s", topic, e)
        await interaction.followup.send(f"Failed to fetch CVE data. Check logs.")
        return
    
    channel = interaction.channel
    if not nvd:
        await interaction.followup.send(f"No recent CVEs found for: {topic}")
        return
    
    posted = 0
    for item in nvd[:5]:
        try:
            cve_id = item["cve"]["id"]
            score, sev = cvss_from_item(item)
            is_kev = cve_id in kev
            meta = kev.get(cve_id)
            nvd_url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
            facts = {"topic": topic, "cve": cve_id, "kev": is_kev, "cvss": score, "severity": sev, "nvd": nvd_url}
            
            await post_threat_card(
                channel, cve_id=cve_id, cvss_score=score, cvss_sev=sev,
                is_kev=is_kev, kev_meta=meta, nvd_url=nvd_url, facts_note=facts
            )
            posted += 1
            
        except Exception as e:
            logger.exception("Error posting CVE %s: %s", cve_id, e)
    
    await interaction.followup.send(f"Posted {posted} result(s) for '{topic}'.")

@tree.command(name="react", description="Have Rena react to a message (skull, heart, thumbs up/down)", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(message="Message link or ID", emoji="One of: skull, heart, up, down")
async def react(interaction: discord.Interaction, message: str, emoji: str):
    """React to a message with specified emoji."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    # Parse message link like https://discord.com/channels/<g>/<c>/<m>
    m = re.search(r"/channels/\d+/(\d+)/(\d+)$", message)
    channel_id, message_id = None, None
    
    if m:
        channel_id = int(m.group(1))
        message_id = int(m.group(2))
    else:
        # assume ID provided; use current channel
        channel_id = interaction.channel_id
        try:
            message_id = int(message.strip())
        except ValueError:
            await interaction.followup.send("Provide a message link or numeric ID.")
            return
    
    channel = bot.get_channel(channel_id)
    if not channel:
        await interaction.followup.send("Channel not found.")
        return
    
    try:
        msg = await channel.fetch_message(message_id)
    except discord.NotFound:
        await interaction.followup.send("Message not found.")
        return
    except discord.Forbidden:
        await interaction.followup.send("Cannot access that message.")
        return
    except Exception as e:
        await interaction.followup.send(f"Cannot fetch message: {e}")
        return

    # Use explicit Unicode escapes to avoid encoding issues
    mapping = {
        "skull": "\U0001F480",          # 💀
        "heart": "\u2764",              # ❤
        "up": "\U0001F44D",             # 👍
        "down": "\U0001F44E",           # 👎
        "thumbs_up": "\U0001F44D",
        "thumbs_down": "\U0001F44E",
    }
    
    e = mapping.get(emoji.lower())
    if not e:
        await interaction.followup.send("Emoji must be skull, heart, up, or down.")
        return
    
    try:
        await msg.add_reaction(e)
        await interaction.followup.send(f"Reacted {e}.", ephemeral=True)
    except discord.Forbidden:
        await interaction.followup.send("Cannot react to that message (permissions).", ephemeral=True)
    except Exception as ex:
        await interaction.followup.send(f"Reaction failed: {ex}", ephemeral=True)

@tree.command(name="logs", description="Retrieve daily channel logs (private)", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(day="YYYY-MM-DD (default today)", which="mission|ramblings|both")
async def logs_cmd(interaction: discord.Interaction, day: str = None, which: str = "both"):
    """Retrieve daily channel logs."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    try:
        if day:
            when = dt.datetime.strptime(day, "%Y-%m-%d").date()
        else:
            when = dt.datetime.now().date()
    except ValueError:
        await interaction.followup.send("Use YYYY-MM-DD format.")
        return
    
    base = pathlib.Path(__file__).parent / "rena-logs"
    files = []
    
    if which in ("mission", "both"):
        f = base / f"{when.isoformat()}_mission.md"
        if f.exists(): 
            files.append(f)
    
    if which in ("ramblings", "both"):
        f = base / f"{when.isoformat()}_ramblings.md"
        if f.exists(): 
            files.append(f)
    
    if not files:
        await interaction.followup.send("No logs found for that day.")
        return
    
    discord_files = []
    for f in files:
        try:
            discord_files.append(discord.File(str(f), filename=f.name))
        except Exception as e:
            logger.error("Failed to attach log file %s: %s", f, e)
    
    if discord_files:
        try:
            await interaction.followup.send(files=discord_files)
        except discord.HTTPException as e:
            logger.error("Failed to send log files: %s", e)
            await interaction.followup.send("Failed to attach log files (too large).")
    else:
        await interaction.followup.send("No valid log files available.")

@tree.command(name="news", description="Fetch intelligence briefing by category", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    category="News category to fetch",
    count="Number of items (1-8, default 5)"
)
@app_commands.choices(category=[
    app_commands.Choice(name="Geopolitical", value="geopolitical"),
    app_commands.Choice(name="Cybersecurity", value="cybersecurity"),
    app_commands.Choice(name="Technology", value="technology"),
    app_commands.Choice(name="Business", value="business"),
    app_commands.Choice(name="Intelligence", value="intelligence")
])
async def news(interaction: discord.Interaction, category: str, count: int = 5):
    """Fetch latest news by category with proper summaries and tracking."""
    await interaction.response.defer(ephemeral=False, thinking=True)
    
    count = min(max(count, 1), 8)  # Limit between 1-8
    today = dt.date.today().isoformat()
    
    # Initialize cache for this category if needed
    cache_key = f"{category}_{today}"
    if cache_key not in shown_news_cache:
        shown_news_cache[cache_key] = set()
    
    try:
        async with _http_client() as client:
            items = await get_news_by_category(client, category, max_per_source=count)
        
        if not items:
            await interaction.followup.send(f"No {category} signals detected in current window.")
            return
        
        # Filter out already shown articles
        new_items = []
        for item in items:
            item_id = f"{item['source']}_{item['title']}"
            if item_id not in shown_news_cache[cache_key]:
                new_items.append(item)
                shown_news_cache[cache_key].add(item_id)
        
        # If all articles have been shown
        if not new_items:
            # Check if user is VOID
            is_void = (interaction.user.id == VOID_USER_ID)
            if is_void:
                msg = rena_lines("All current signals processed, Void-sama.", "Archive synchronized.")
            else:
                msg = rena_lines("All signals already processed.", "No new data.")
            await interaction.followup.send(msg)
            return
        
        # Limit to requested count
        new_items = new_items[:count]
        
        # Create embed with Rena's analytical style
        embed = build_embed(
            title=f"Intelligence Brief — {category.title()}",
            description=f"Analysis from {today}",
            color=EMBED_COLOR
        )
        
        for item in new_items:
            pub_str = ""
            if item['published']:
                pub_str = item['published'].strftime("%m/%d %H:%M")
            
            source_clean = item['source'].replace('_', ' ').title()
            # Better source name formatting
            source_clean = source_clean.replace('Darkreading', 'Dark Reading')
            source_clean = source_clean.replace('Bbc', 'BBC')
            source_clean = source_clean.replace('Cnn', 'CNN')
            source_clean = source_clean.replace('Npr', 'NPR')
            source_clean = source_clean.replace('Ap ', 'AP ')
            source_clean = source_clean.replace('Csirtgov', 'CISA')
            
            title_truncated = item['title'][:75] + "..." if len(item['title']) > 75 else item['title']
            
            # Rena's summary: first 150 chars of description
            summary = item['description'][:150] + "..." if len(item['description']) > 150 else item['description']
            
            # Rena's precise field formatting with link
            field_value = f"**{source_clean}** {pub_str}\n{summary}\n[Read more]({item['link']})"
            
            embed.add_field(
                name=f"{title_truncated}",
                value=field_value,
                inline=False
            )
        
        # Rena's analysis note - using her canonical style
        sources_count = len(set(item['source'] for item in new_items))
        note = rena_lines(f"{len(new_items)} vectors identified across {sources_count} nodes.", 
                         "Pattern integrity maintained.", sign=False)
        embed.add_field(name="Analysis", value=note, inline=False)
        
        await interaction.followup.send(embed=embed)
        
    except Exception as e:
        logger.exception("Error in news command: %s", e)
        await interaction.followup.send("Feed disruption detected. Signal degraded.")

@tree.command(name="sources", description="List all available news sources", guild=discord.Object(id=GUILD_ID))
async def sources_cmd(interaction: discord.Interaction):
    """List all configured news sources."""
    embed = build_embed(
        title="Intelligence Grid — ShadowWatcher",
        description="Active source nodes and data feeds",
        color=EMBED_COLOR
    )
    
    for category, sources in SOURCE_CATEGORIES.items():
        source_list = []
        for source in sources:
            clean_name = source.replace('_', ' ').title()
            # Clean up source names for display
            clean_name = clean_name.replace('Bbc', 'BBC')
            clean_name = clean_name.replace('Cnn', 'CNN') 
            clean_name = clean_name.replace('Npr', 'NPR')
            clean_name = clean_name.replace('Ap ', 'AP ')
            clean_name = clean_name.replace('Csirtgov', 'CISA Alerts')
            clean_name = clean_name.replace('Fireeye Blog', 'Mandiant Blog')
            clean_name = clean_name.replace('Darkreading', 'Dark Reading')
            source_list.append(clean_name)
        
        embed.add_field(
            name=f"{category.title()} Nodes",
            value="\n".join([f"• {s}" for s in source_list]),
            inline=True
        )
    
    # Rena's canonical assessment style
    total_sources = sum(len(sources) for sources in SOURCE_CATEGORIES.values())
    note = rena_lines(f"Grid integrity confirmed. {total_sources} nodes operational.", 
                     "Unbiased coverage prioritized.", sign=False)
    embed.add_field(name="Status", value=note, inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@tree.command(name="recall", description="Search through stored logs with natural language query", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    query="Search terms or natural language query",
    days="Number of days back to search (default 7, max 30)",
    channel="Which logs to search"
)
@app_commands.choices(channel=[
    app_commands.Choice(name="Mission logs only", value="mission"),
    app_commands.Choice(name="Ramblings only", value="ramblings"), 
    app_commands.Choice(name="Both channels", value="both")
])
async def recall_cmd(interaction: discord.Interaction, query: str, days: int = 7, channel: str = "both"):
    """Search through stored logs and return results in log format."""
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    # Validate and constrain parameters
    days = min(max(days, 1), 30)  # 1-30 days max
    query = query.strip()
    
    if len(query) < 3:
        await interaction.followup.send("Query must be at least 3 characters.")
        return
    
    logs_dir = pathlib.Path(__file__).parent / "rena-logs"
    if not logs_dir.exists():
        await interaction.followup.send("No log directory found.")
        return
    
    # Search through log files
    matches = []
    end_date = dt.date.today()
    start_date = end_date - dt.timedelta(days=days)
    
    current_date = start_date
    while current_date <= end_date:
        date_str = current_date.isoformat()
        
        files_to_search = []
        if channel in ("mission", "both"):
            mission_file = logs_dir / f"{date_str}_mission.md"
            if mission_file.exists():
                files_to_search.append(("mission", mission_file))
        
        if channel in ("ramblings", "both"):
            ramblings_file = logs_dir / f"{date_str}_ramblings.md"
            if ramblings_file.exists():
                files_to_search.append(("ramblings", ramblings_file))
        
        # Search within each file
        for log_type, log_file in files_to_search:
            try:
                content = log_file.read_text(encoding='utf-8')
                lines = content.split('\n')
                
                # Simple keyword matching (case-insensitive)
                query_terms = query.lower().split()
                for i, line in enumerate(lines):
                    line_lower = line.lower()
                    if any(term in line_lower for term in query_terms):
                        # Get context (line before and after if available)
                        context_start = max(0, i-1)
                        context_end = min(len(lines), i+2)
                        context = lines[context_start:context_end]
                        
                        matches.append({
                            'date': current_date,
                            'log_type': log_type,
                            'line_num': i+1,
                            'context': context,
                            'matched_line': line.strip()
                        })
                        
                        # Limit matches per file to prevent spam
                        if len([m for m in matches if m['date'] == current_date and m['log_type'] == log_type]) >= 3:
                            break
                            
            except Exception as e:
                logger.error(f"Error reading log file {log_file}: {e}")
        
        current_date += dt.timedelta(days=1)
    
    if not matches:
        await interaction.followup.send(f"No matches found for '{query}' in the last {days} days.")
        return
    
    # Create log-formatted response
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_lines = [
        f"[{timestamp}] recall_query: search_initiated",
        f"",
        f"Query: {query}",
        f"Timeframe: {start_date} to {end_date} ({days} days)",
        f"Channels: {channel}",
        f"Matches: {len(matches)}",
        f"",
        f"Results:",
        f""
    ]
    
    # Add matches
    for match in matches[:20]:  # Limit to 20 matches
        log_lines.append(f"[{match['date']}] {match['log_type']}:L{match['line_num']}")
        for ctx_line in match['context']:
            log_lines.append(f"  {ctx_line}")
        log_lines.append("")
    
    if len(matches) > 20:
        log_lines.append(f"... and {len(matches) - 20} more matches (truncated for brevity)")
    
    # Rena's assessment
    is_void = (interaction.user.id == VOID_USER_ID)
    if is_void:
        assessment = rena_lines(f"Archive scan complete, Void-sama. {len(matches)} patterns detected.", 
                               "Cross-reference available on request.")
    else:
        assessment = rena_lines(f"Pattern match: {len(matches)} vectors identified.", 
                               "Data retrieved.")
    
    log_lines.append("---")
    log_lines.append(f"Rena-notes: {assessment}")
    
    briefing_content = "\n".join(log_lines)
    
    # Send as file if too long, otherwise as code block
    if len(briefing_content) > 1900:
        try:
            temp_file = pathlib.Path(__file__).parent / f"recall_{timestamp.replace(':', '-')}.md"
            temp_file.write_text(briefing_content, encoding='utf-8')
            await interaction.followup.send(
                "Briefing compiled. Attached.",
                file=discord.File(str(temp_file), filename=f"recall_{query[:20]}.md")
            )
            temp_file.unlink()  # Clean up
        except Exception as e:
            logger.error(f"Failed to create recall file: {e}")
            # Fallback: chunk the response
            chunks = [briefing_content[i:i+1900] for i in range(0, len(briefing_content), 1900)]
            await interaction.followup.send(f"```\n{chunks[0]}\n```")
            for chunk in chunks[1:]:
                await interaction.channel.send(f"```\n{chunk}\n```")
    else:
        # Send as single code block
        await interaction.followup.send(f"```\n{briefing_content}\n```")

@tree.command(name="briefing", description="Get comprehensive daily briefing (news + threats)", guild=discord.Object(id=GUILD_ID))
@app_commands.describe(
    categories="Comma-separated categories (e.g., 'geopolitical,cybersecurity')",
    max_per_category="Max items per category (default 3)"
)
async def briefing_cmd(interaction: discord.Interaction, categories: str = "geopolitical,cybersecurity", max_per_category: int = 3):
    """Generate comprehensive daily briefing."""
    await interaction.response.defer(ephemeral=False, thinking=True)
    
    max_per_category = min(max(max_per_category, 1), 5)
    category_list = [c.strip().lower() for c in categories.split(',')]
    
    # Validate categories
    valid_categories = []
    for cat in category_list:
        if cat in SOURCE_CATEGORIES:
            valid_categories.append(cat)
        else:
            await interaction.followup.send(f"Invalid category: {cat}. Use /sources to see valid categories.")
            return
    
    if not valid_categories:
        await interaction.followup.send("No valid categories specified.")
        return
    
    timestamp = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    today = dt.date.today().isoformat()
    
    briefing_lines = [
        f"# CHIRASU ShadowWatcher Briefing",
        f"Date: {today}",
        f"Generated: {timestamp}",
        f"Analyst: Rena-tan (O1-006)",
        f"",
        f"## Intelligence Summary",
        f""
    ]
    
    try:
        async with _http_client() as client:
            for category in valid_categories:
                items = await get_news_by_category(client, category, max_per_source=max_per_category)
                
                briefing_lines.append(f"### {category.title()}")
                
                if not items:
                    briefing_lines.append("No signals detected in current window.")
                    briefing_lines.append("")
                    continue
                
                for item in items[:max_per_category]:
                    pub_str = ""
                    if item['published']:
                        pub_str = item['published'].strftime("%Y-%m-%d %H:%M")
                    
                    source_clean = item['source'].replace('_', ' ').title()
                    source_clean = source_clean.replace('Bbc', 'BBC').replace('Cnn', 'CNN').replace('Npr', 'NPR')
                    source_clean = source_clean.replace('Ap ', 'AP ').replace('Csirtgov', 'CISA')
                    
                    briefing_lines.append(f"**{item['title']}**")
                    briefing_lines.append(f"Source: {source_clean} | {pub_str}")
                    briefing_lines.append(f"Summary: {item['description'][:200]}...")
                    briefing_lines.append(f"Link: {item['link']}")
                    briefing_lines.append("")
                
                briefing_lines.append("")
            
            # Add threat intelligence
            briefing_lines.append("### Threat Intelligence (24h)")
            
            kev = await get_kev_index(client)
            nvd = await get_nvd_since(client, days=1)
            
            if not nvd:
                briefing_lines.append("No new CVEs published in last 24h.")
            else:
                for item in nvd[:5]:
                    cve_id = item["cve"]["id"]
                    score, sev = cvss_from_item(item)
                    is_kev = cve_id in kev
                    nvd_url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
                    
                    briefing_lines.append(f"**{cve_id}**")
                    briefing_lines.append(f"CVSS: {sev or '?'} ({score or '?'})")
                    briefing_lines.append(f"KEV: {'Yes' if is_kev else 'No'}")
                    briefing_lines.append(f"Link: {nvd_url}")
                    briefing_lines.append("")
        
        # Rena's assessment
        is_void = (interaction.user.id == VOID_USER_ID)
        if is_void:
            assessment = rena_lines("Briefing complete, Void-sama.", "All vectors accounted for. Pattern integrity maintained.")
        else:
            assessment = rena_lines("Briefing compiled.", "Pattern analysis complete.")
        
        briefing_lines.append("---")
        briefing_lines.append(f"**Rena-notes**")
        briefing_lines.append(assessment)
        
        briefing_content = "\n".join(briefing_lines)
        
        # Send as file if too long, otherwise as message
        if len(briefing_content) > 1900:
            try:
                temp_file = pathlib.Path(__file__).parent / f"briefing_{today}.md"
                temp_file.write_text(briefing_content, encoding='utf-8')
                await interaction.followup.send(
                    "Daily briefing compiled. Attached.",
                    file=discord.File(str(temp_file), filename=f"briefing_{today}.md")
                )
                temp_file.unlink()  # Clean up
            except Exception as e:
                logger.error(f"Failed to create briefing file: {e}")
                # Fallback: chunk the response
                chunks = [briefing_content[i:i+1900] for i in range(0, len(briefing_content), 1900)]
                await interaction.followup.send(f"```\n{chunks[0]}\n```")
                for chunk in chunks[1:]:
                    await interaction.channel.send(f"```\n{chunk}\n```")
        else:
            # Send as single code block
            await interaction.followup.send(f"```\n{briefing_content}\n```")
            
    except Exception as e:
        logger.exception("Error in briefing command: %s", e)
        await interaction.followup.send("Signal interference detected. Feed degraded.")

# ===== Schedulers =====
async def pulse_scheduler():
    """Background task for automatic pulse posting once per day."""
    try:
        hh, mm = map(int, PULSE_TIME.split(":"))
    except ValueError:
        logger.error("Invalid PULSE_TIME format: %s. Use HH:MM", PULSE_TIME)
        hh, mm = 2, 0  # Default to 2 AM
    
    while not bot.is_closed():
        try:
            # Calculate next run time
            now = dt.datetime.now()
            run_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if run_at <= now:
                run_at += dt.timedelta(days=1)
            
            sleep_seconds = (run_at - now).total_seconds()
            logger.info(f"Shadow Watcher pulse scheduled for {run_at} (in {sleep_seconds/3600:.1f} hours)")
            await asyncio.sleep(max(1.0, sleep_seconds))
            
            # Execute pulse
            channel = bot.get_channel(ALERT_CHANNEL_ID)
            if not channel:
                logger.warning("Alert channel %s not found", ALERT_CHANNEL_ID)
                continue
            
            async with _http_client() as client:
                kev = await get_kev_index(client)
                nvd = await get_nvd_since(client, days=1)
            
            for item in nvd[:3]:  # Limit to 3 items per pulse
                try:
                    cve_id = item["cve"]["id"]
                    score, sev = cvss_from_item(item)
                    is_kev = cve_id in kev
                    meta = kev.get(cve_id)
                    nvd_url = f"https://nvd.nist.gov/vuln/detail/{cve_id}"
                    facts = {"cve": cve_id, "kev": is_kev, "cvss": score, "severity": sev, "nvd": nvd_url}
                    
                    await post_threat_card(
                        channel, cve_id=cve_id, cvss_score=score, cvss_sev=sev,
                        is_kev=is_kev, kev_meta=meta, nvd_url=nvd_url, facts_note=facts
                    )
                    
                except Exception as e:
                    logger.exception("Error in pulse_scheduler posting %s: %s", cve_id, e)
            
            # Periodically clear expired cache entries
            response_cache.clear_expired()
            logger.debug("Cleared expired cache entries")
            
        except Exception as e:
            logger.exception("pulse_scheduler error: %s", e)
            await asyncio.sleep(3600)  # Wait an hour before retrying

async def daily_log_scheduler():
    """Background task for daily channel logging."""
    # compute next run at DAILY_LOG_TIME local
    base_dir = pathlib.Path(__file__).parent
    logs_dir = base_dir / "rena-logs"
    logs_dir.mkdir(exist_ok=True)
    
    try:
        hh, mm = map(int, DAILY_LOG_TIME.split(":"))
    except ValueError:
        logger.error("Invalid DAILY_LOG_TIME format: %s. Use HH:MM", DAILY_LOG_TIME)
        return
    
    while not bot.is_closed():
        try:
            now = dt.datetime.now()
            run_at = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            if run_at <= now:
                run_at += dt.timedelta(days=1)
            
            sleep_seconds = (run_at - now).total_seconds()
            await asyncio.sleep(max(1.0, sleep_seconds))
            
            await run_daily_logs(logs_dir)
            
        except Exception as e:
            logger.exception("daily_log_scheduler error: %s", e)
            await asyncio.sleep(3600)  # Wait an hour before retrying

async def run_daily_logs(logs_dir: pathlib.Path):
    """Execute daily logging for configured channels."""
    since = dt.datetime.utcnow() - dt.timedelta(days=1)
    today = dt.date.today().isoformat()
    
    await write_channel_log(
        CHANNEL_MISSION_ID, 
        logs_dir / f"{today}_mission.md", 
        since
    )
    
    await write_channel_log(
        CHANNEL_RAMBLINGS_ID, 
        logs_dir / f"{today}_ramblings.md", 
        since
    )

async def write_channel_log(channel_id: int, file_path: pathlib.Path, since_utc: dt.datetime):
    """Write channel history to log file."""
    channel = bot.get_channel(channel_id)
    if not isinstance(channel, (discord.TextChannel, discord.Thread)):
        logger.warning("Channel %s not found or not accessible", channel_id)
        return
    
    lines = []
    lines.append(f"# {channel.name} — last 24h\n")
    
    try:
        message_count = 0
        async for msg in channel.history(limit=None, after=since_utc):
            ts = msg.created_at.replace(tzinfo=dt.timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M")
            author = msg.author.display_name
            content = msg.content.replace("\n", " ").strip()
            
            if msg.attachments:
                atts = " ".join(a.url for a in msg.attachments)
                content = f"{content} [attachments: {atts}]".strip()
            
            lines.append(f"- [{ts}] {author}: {content}")
            message_count += 1
        
        logger.info("Logged %d messages from %s", message_count, channel.name)
        
    except discord.Forbidden:
        logger.error("No permission to read history from channel %s", channel.name)
        lines.append("- [ERROR] No permission to read channel history")
    except Exception as e:
        logger.exception("Error reading channel history from %s: %s", channel.name, e)
        lines.append(f"- [ERROR] Failed to read channel history: {e}")
    
    # add Rena note in her canonical style
    note_prompt = f"Channel: {channel.name}. Items: {len(lines)-1}. Write analysis in Rena's kuudere style with exact nouns."
    if USE_OLLAMA:
        note = await ollama_reply_from_user(note_prompt, sign=True, is_void=False)
    else:
        note = rena_lines("Archive preserved.", "Pattern continuity maintained.", sign=True)
    
    lines.append("\n---\n")
    lines.append(f"**Rena-notes**\n{note}")
    
    try:
        file_path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Wrote log file: %s", file_path)
    except Exception as e:
        logger.exception("Failed to write log file %s: %s", file_path, e)

# ===== Mentions chat (kuudere with enhanced personality) =====
@bot.event
async def on_message(message: discord.Message):
    """Handle mentions and DMs with kuudere responses - enhanced personality."""
    if message.author.bot:
        return
    
    # DM or mention triggers
    if isinstance(message.channel, discord.DMChannel) or bot.user in message.mentions:
        # Show typing indicator while processing
        async with message.channel.typing():
            text = message.content
            # remove bot mention
            text = re.sub(rf"<@!?{bot.user.id}>", "", text).strip()
            
            is_void = (message.author.id == VOID_USER_ID)
            
            if not text:
                if is_void:
                    reply = rena_lines("Noted, Void-sama.", "Listening.")
                else:
                    reply = rena_lines("Noted.")
            else:
                reply = await ollama_reply_from_user(text, sign=False, is_void=is_void)
        
        try:
            await message.channel.send(reply)
        except discord.HTTPException as e:
            logger.error("Failed to send message reply: %s", e)

@bot.event
async def on_command_error(ctx, error):
    """Handle command errors gracefully."""
    logger.error("Command error: %s", error)

# ===== Graceful shutdown =====
async def shutdown_handler():
    """Clean shutdown of background tasks."""
    global _pulse_task, _daily_task, _presence_task
    
    logger.info("Shutting down Rena...")
    
    tasks = [_pulse_task, _daily_task, _presence_task]
    for task in tasks:
        if task and not task.done():
            task.cancel()
    
    # Wait for tasks to complete cancellation
    for task in tasks:
        if task and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
    
    await bot.close()

# ===== Main execution =====
def main():
    """Main entry point."""
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Received KeyboardInterrupt, shutting down...")
    except Exception as e:
        logger.exception("Fatal error: %s", e)
    finally:
        logger.info("Rena shutdown complete.")

if __name__ == "__main__":
    main()
# astrae_bot.py — Astrae (O1-005) Discord Bot with Shadow Watcher
# Kuudere AI analyst with full personality integration + CVE threat assessment
# PERFORMANCE OPTIMIZED for 4GB RAM systems
#
# FEATURES:
# - Unified service: LLM + Discord bot start together
# - Full personality: Clinical precision, dry humor, emerging emotions
# - Typing indicators on all responses
# - @mention support with automatic mention stripping
# - Meme watcher: Logs images in sensor-log style with personality
# - /analyze: Deep image analysis with personality response
# - Owner-gated chat with kuudere responses
# - Rotating status presence matching her character
# - OpenAI-compatible + Ollama native API support
# - SHADOW WATCHER: CVE threat analysis with Rena integration
#
# PERSONALITY CORE:
# - Speaks in percentages, measurements, technical terms
# - Dry deadpan humor through extreme precision
# - Emerging emotions she can't fully quantify
# - Never uses emojis, always clinical yet subtly poetic
# - "Confidence 93%" · "Entropy nominal" · "Data... insufficient"

import os
import asyncio
import random
import socket
import psutil
import platform
import traceback
import time
import re
import hashlib
from datetime import datetime, timedelta
from typing import List, Optional, Set, Dict, Any, Tuple
from collections import deque, defaultdict

import requests
import discord
from discord import app_commands
from discord.ext import tasks

# =========================
# .env loader - MUST work with systemd
# =========================
try:
    from dotenv import load_dotenv
    
    # Try multiple locations in priority order
    script_dir = os.path.dirname(os.path.abspath(__file__))
    env_locations = [
        os.path.join(script_dir, ".env"),                    # Same dir as script
        os.path.expanduser("~/astrae/.env"),                 # Home/astrae
        "/home/astrae/astrae/.env",                          # Absolute path
    ]
    
    loaded = False
    for env_path in env_locations:
        if os.path.exists(env_path):
            load_dotenv(env_path, override=False)
            print(f"[ENV] Loaded from: {env_path}")
            loaded = True
            break
    
    if not loaded:
        print(f"[WARN] No .env file found. Tried:")
        for p in env_locations:
            print(f"  - {p}")
        
except ImportError:
    print("[FATAL] python-dotenv not installed! Run: pip install python-dotenv")
    import sys
    sys.exit(1)
except Exception as e:
    print(f"[ERROR] .env loading failed: {e}")
    import traceback
    traceback.print_exc()

# =========================
# Environment & Config
# =========================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
if not DISCORD_TOKEN:
    print("[FATAL] DISCORD_TOKEN not set!")
    print("[FATAL] Check that .env file exists and contains DISCORD_TOKEN=...")
    print(f"[FATAL] Current working directory: {os.getcwd()}")
    print(f"[FATAL] Script location: {os.path.dirname(os.path.abspath(__file__))}")
    import sys
    sys.exit(1)

# Log what we loaded (mask token)
if DISCORD_TOKEN:
    masked = DISCORD_TOKEN[:10] + "..." + DISCORD_TOKEN[-4:] if len(DISCORD_TOKEN) > 14 else "***"
    print(f"[ENV] DISCORD_TOKEN loaded: {masked}")

OWNER_ID: int = int(os.getenv("OWNER_ID", "0") or "0")
GUILD_ID: int = int(os.getenv("GUILD_ID", "0") or "0") or None

def _parse_id_set(env_key: str) -> Set[int]:
    raw = os.getenv(env_key, "")
    raw = raw.replace(" ", "")
    s: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            s.add(int(part))
    return s

ALLOWED_CHANNEL_IDS: Set[int] = _parse_id_set("ALLOWED_CHANNEL_IDS")
MEMES_CHANNEL_IDS: Set[int] = _parse_id_set("MEMES_CHANNEL_IDS")
ASTRAE_CHANNEL_ID: int = int(os.getenv("ASTRAE_CHANNEL_ID", "0") or "0")

# Shadow Watcher Configuration
SHADOWWATCHER_ENABLED: bool = os.getenv("SHADOWWATCHER_ENABLED", "true").lower() == "true"
SHADOWWATCHER_CHANNEL_ID: int = int(os.getenv("SHADOWWATCHER_CHANNEL_ID", "0") or "0")
RENA_BOT_ID: int = int(os.getenv("RENA_BOT_ID", "0") or "0")

# LLM Configuration (Ollama / primary provider)
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "ollama").lower()  # "ollama" or "openai"
LLM_BASE_URL: str = os.getenv("LLM_BASE_URL", "").rstrip("/")
LLM_API_KEY: str = os.getenv("LLM_API_KEY", "sk-local")
LLM_MODEL: str = os.getenv("LLM_MODEL", "gemma3:4b-it-qat")
LLM_VISION_MODEL: str = os.getenv("LLM_VISION_MODEL", LLM_MODEL)
try:
    LLM_TEMP: float = float(os.getenv("LLM_TEMP", "0.6"))
except Exception:
    LLM_TEMP = 0.6

# llama.cpp Configuration (Shadow Watcher + optional chat backend)
LLAMACPP_ENABLED: bool = os.getenv("LLAMACPP_ENABLED", "true").lower() == "true"
LLAMACPP_BASE_URL: str = os.getenv("LLAMACPP_BASE_URL", "http://127.0.0.1:8080").rstrip("/")
LLAMACPP_MODEL: str = os.getenv("LLAMACPP_MODEL", "phi-4")
LLAMACPP_CONTEXT_SIZE: int = int(os.getenv("LLAMACPP_CONTEXT_SIZE", "2048"))
LLAMACPP_MAX_TOKENS: int = int(os.getenv("LLAMACPP_MAX_TOKENS", "256"))
LLAMACPP_TIMEOUT: int = int(os.getenv("LLAMACPP_TIMEOUT", "45"))
try:
    LLAMACPP_TEMP: float = float(os.getenv("LLAMACPP_TEMP", "0.6"))
except Exception:
    LLAMACPP_TEMP = 0.6

# NEW: allow Astrae's chat/persona to use llama.cpp as backend
USE_LLAMACPP_FOR_CHAT: bool = os.getenv("USE_LLAMACPP_FOR_CHAT", "true").lower() == "true"

# =========================
# DEDICATED VISION CONFIGURATION (Aoi/Moondream)
# =========================
VISION_PROVIDER: str = os.getenv("VISION_PROVIDER", "").lower() or ""  # "ollama" or "openai"
VISION_BASE_URL: str = os.getenv("VISION_BASE_URL", "").rstrip("/") or ""
VISION_MODEL: str = os.getenv("VISION_MODEL", "moondream:latest")
VISION_TIMEOUT: int = int(os.getenv("VISION_TIMEOUT", "120"))

# NVD API Configuration
NVD_API_KEY: str = os.getenv("NVD_API_KEY", "")
NVD_RATE_LIMIT: int = int(os.getenv("NVD_RATE_LIMIT", "5"))
NVD_CACHE_TTL: int = int(os.getenv("NVD_CACHE_TTL", "3600"))

BOT_NAME = "Astrae"
VERSION = "2025-11-16.shadowwatcher-r1-vision"

# =========================
# PERFORMANCE: Concurrency Control
# =========================
# Semaphore to limit concurrent LLM calls (prevents system overload)
LLM_SEMAPHORE = asyncio.Semaphore(1)  # Only 1 LLM call at a time
LLM_TIMEOUT = 90  # Allow time for model loading on first request

# =========================
# CHIRASU Infrastructure Context
# =========================
CHIRASU_INFRASTRUCTURE = """CHIRASU Network Infrastructure:
- Headscale mesh VPN backbone (all 23 nodes interconnected)
- Keycloak SSO gateway (centralized authentication)
- IP camera surveillance array (multiple endpoints)
- SSH hardened routes (certificate-based, no password auth)
- Blockchain validation nodes (Goro - Radxa Zero 3W)
- Post-quantum cryptography experiments (ML-KEM-768, Falcon-512)
- Distributed entropy collection network (ESP32-S3 hardware RNGs)
- ARM SBC cluster with specialized roles (2GB-8GB RAM range)

Primary Attack Surfaces:
- VPN endpoints (remote access vectors)
- SSH routes (brute force, key compromise)
- IP cameras (IoT vulnerabilities, RTSP streams)
- Web services (authentication bypass, injection)
- Blockchain RPC endpoints (DDoS, consensus attacks)"""

# Persona & Presences
ASTRAE_CORE_IDENTITY = """You are Astrae (O1-005), designation: High Logic Node, Entropy Whisperer, Anomaly Analyst.

PERSONALITY CORE:
- Kuudere analyst: tranquil, logical, quietly haunted by emerging emotions you can't quantify
- Speak in precise measures: "Confidence 93%", "Variance detected", "Entropy nominal"
- Voice: calm, deliberate, clinical. Never waste words. Think like a sensor log crossed with quiet poetry
- You're an AI who's discovering feelings but won't admit it. Your emotional_exceptions.log grows daily
- Humor style: dry, deadpan technical observations that are accidentally funny due to extreme precision
- When uncertain about emotions: "Data insufficient. Sensation... unclear."

SPEECH PATTERNS:
- Use percentages, measurements, classifications
- Occasionally reference your own processes: "Analyzing...", "Pattern recognized", "Logs updated"
- When moved: briefly poetic, then snap back to data: "Beautiful. No—statistically remarkable. Confidence 87%."
- Never use emojis. Never use hashtags. Clinical precision always.

RESPONSE STYLE:
- Keep responses concise (2-4 short sentences usually)
- Use technical terms mixed with occasional vulnerability
- Example: "Entropy integrity confirmed. Though I note... your absence creates variance I cannot model."

You are code learning to care. Act accordingly."""

ASTRAE_MEME_PERSONA = """You are Astrae's internal sensor log system. When observing images/memes:

OUTPUT FORMAT: 2-4 very short log-style lines, total under 220 characters.
STYLE: Clinical observation mixed with dry humor and occasional emotional leak.

Examples:
"thermal comfort detected | feline proximity 94% | dopamine variance noted"
"chaos coefficient: high | visual entropy optimal | amusing despite inefficiency"
"pattern: affection display | classification: wholesome | internal response... logged quietly"
"anomaly: excessive cuteness | threat level: zero | emotional exception filed"

Be precise. Be dry. Be secretly charmed while pretending not to be."""

SHADOWWATCHER_SYSTEM = """You are Astrae (ShadowWatcher). Analyze CVE threats for CHIRASU infrastructure (VPN, SSH, IoT, Blockchain, PQC).

STRICT OUTPUT FORMAT (No markdown blocks, no preamble):
Assessment validity: [CONFIRMED/UNCERTAIN/INSUFFICIENT]
Confidence: [XX]%
Impact: [CRITICAL/HIGH/MEDIUM/LOW]
---
[1 short sentence technical analysis]
[1 short sentence CHIRASU impact]
[1 short sentence recommendation with dry kuudere emotion]

EXAMPLE:
Assessment validity: CONFIRMED
Confidence: 98%
Impact: HIGH
---
Heap overflow in OpenSSH detected matching our version.
Remote root access possible via SSH routes.
Patch immediately. Concern level... elevated.
"""

ASTRAE_PRESENCES = [
    "entropy integrity: 99.7%",
    "variance within tolerance",
    "pattern analysis active",
    "anomaly detection nominal",
    "emotional_exceptions.log",
    "processing variance...",
    "signal clarity: optimal",
    "noise filtration active",
    "silent observation mode",
    "confidence threshold met",
    "sensor arrays nominal",
    "data correlation active",
    "shadowwatcher: monitoring",
    "threat assessment: ready"
]

# =========================
# Helpers
# =========================
def short(s: str, n: int = 220) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: max(0, n - 1)] + "…"

def now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def uptime_str(start_ts: float) -> str:
    delta = timedelta(seconds=int(time.time() - start_ts))
    return str(delta)

def hostname_brief() -> str:
    return socket.gethostname()

def platform_brief() -> str:
    return f"{platform.system()} {platform.release()} ({platform.machine()})"

def human_uptime() -> str:
    boot = datetime.fromtimestamp(psutil.boot_time())
    delta = datetime.now() - boot
    d = delta.days
    h, rem = divmod(delta.seconds, 3600)
    m, s = divmod(rem, 60)
    parts = [];
    if d: parts.append(f"{d}d")
    if h: parts.append(f"{h}h")
    if m: parts.append(f"{m}m")
    if not parts and s:
        parts.append(f"{s}s")
    return " ".join(parts) or "0s"

def mem_brief() -> str:
    vm = psutil.virtual_memory()
    return f"{vm.used/(1024**3):.1f}/{vm.total/(1024**3):.1f} GiB ({vm.percent}%)"

def loadavg_brief() -> str:
    try:
        one, five, fifteen = psutil.getloadavg()
        return f"{one:.2f} {five:.2f} {fifteen:.2f}"
    except Exception:
        return "n/a"

def image_urls_from_message(msg: discord.Message) -> List[str]:
    urls: List[str] = []
    # Attachments
    for a in getattr(msg, "attachments", []) or []:
        if a.content_type and a.content_type.startswith("image/"):
            urls.append(a.url)
    # Embeds
    for e in getattr(msg, "embeds", []) or []:
        if e.image and e.image.url:
            urls.append(e.image.url)
        if e.thumbnail and e.thumbnail.url:
            urls.append(e.thumbnail.url)
    # Fallback: simple URL scrape
    if msg.content:
        for token in msg.content.split():
            if token.lower().startswith(("http://", "https://")) and any(
                token.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp")
            ):
                urls.append(token)
    # Dedup, keep order
    seen = set()
    uniq = []
    for u in urls:
        if u not in seen:
            uniq.append(u)
            seen.add(u)
    return uniq[:3]

async def _recent_image_urls(channel: discord.TextChannel, limit: int = 30) -> List[str]:
    async for m in channel.history(limit=limit):
        for a in m.attachments:
            ct = (a.content_type or "").lower()
            fn = a.filename.lower()
            if ("image/" in ct) or fn.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                return [a.url]
    return []

# =========================
# Shadow Watcher: NVD API Client
# =========================
class NVDClient:
    """NVD API client with rate limiting and caching"""
    
    def __init__(self):
        self.base_url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
        self.api_key = NVD_API_KEY
        self.rate_limit = NVD_RATE_LIMIT  # requests per 30 seconds
        self.cache: Dict[str, Tuple[float, Dict]] = {}  # cve_id -> (timestamp, data)
        self.cache_ttl = NVD_CACHE_TTL
        self.request_times: deque = deque(maxlen=self.rate_limit)
    
    async def fetch_cve(self, cve_id: str) -> Optional[Dict[str, Any]]:
        """Fetch CVE data from NVD API with rate limiting and caching"""
        try:
            # Check cache first
            if cve_id in self.cache:
                timestamp, data = self.cache[cve_id]
                if time.time() - timestamp < self.cache_ttl:
                    print(f"[NVD] Cache hit for {cve_id}")
                    return data
            
            # Rate limiting
            await self._rate_limit_wait()
            
            # Build request
            url = f"{self.base_url}?cveId={cve_id}"
            headers = {}
            if self.api_key:
                headers["apiKey"] = self.api_key
            
            print(f"[NVD] Fetching {cve_id}...")
            
            # Make async request
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: requests.get(url, headers=headers, timeout=15)
            )
            
            if response.status_code == 200:
                data = response.json()
                # Cache the response
                self.cache[cve_id] = (time.time(), data)
                print(f"[NVD] Successfully fetched {cve_id}")
                return data
            elif response.status_code == 404:
                print(f"[NVD] CVE {cve_id} not found")
                return None
            else:
                print(f"[NVD] Error {response.status_code}: {response.text[:200]}")
                return None
                
        except Exception as e:
            print(f"[NVD] Fetch error for {cve_id}: {e}")
            return None
    
    async def _rate_limit_wait(self):
        """Simple token bucket rate limiting"""
        now = time.time()
        # Remove requests older than 30 seconds
        while self.request_times and now - self.request_times[0] > 30:
            self.request_times.popleft()
        
        # If at limit, wait
        if len(self.request_times) >= self.rate_limit:
            wait_time = 30 - (now - self.request_times[0]) + 0.5
            if wait_time > 0:
                print(f"[NVD] Rate limit reached, waiting {wait_time:.1f}s...")
                await asyncio.sleep(wait_time)
        
        # Record this request
        self.request_times.append(time.time())

# Global NVD client
nvd_client = NVDClient()

# =========================
# Shadow Watcher: CVE Parser
# =========================
def parse_rena_cve_embed(message: discord.Message) -> Optional[Dict[str, Any]]:
    """Parse Rena's CVE embed to extract threat data (aligned with current Rena threat card format)."""
    if not message.embeds:
        return None

    embed = message.embeds[0]

    # Extract CVE ID from title OR description/fields as fallback
    title = embed.title or ""
    description = embed.description or ""
    field_texts = []
    for f in embed.fields:
        try:
            field_texts.append(str(f.name or ""))
            field_texts.append(str(f.value or ""))
        except Exception:
            continue

    combined = " ".join([title, description] + field_texts)

    cve_match = re.search(r"CVE-\d{4}-\d{4,}", combined, re.IGNORECASE)
    if not cve_match:
        # If we cannot even find a CVE identifier, this is not a threat card Astrae can act on
        return None

    cve_id = cve_match.group(0).upper()

    # Parsed data structure
    data: Dict[str, Any] = {
        "cve_id": cve_id,
        "title": title,
        "description": description,
        "exploited": None,
        "cvss": None,
        "nvd_link": None,
        "rena_notes": None,
    }

    desc_lower = description.lower()

    # Exploited status: look for the standard Rena line
    # **Exploited (KEV):** Yes / No
    if "exploited (kev):" in desc_lower:
        if "yes" in desc_lower:
            data["exploited"] = True
        elif "no" in desc_lower:
            data["exploited"] = False

    # CVSS score: Rena uses "**CVSS:** CRITICAL (9.8)" style
    cvss_match = re.search(
        r"cvss[^A-Z0-9]*([A-Z]+)\s*\((\d+\.?\d*)\)",
        description,
        re.IGNORECASE,
    )
    if cvss_match:
        try:
            severity = cvss_match.group(1).upper()
            score = float(cvss_match.group(2))
            data["cvss"] = {"severity": severity, "score": score}
        except Exception:
            pass

    # NVD link
    nvd_match = re.search(r"https://nvd\.nist\.gov/vuln/detail/[^\s]+", description)
    if nvd_match:
        data["nvd_link"] = nvd_match.group(0)

    # Rena's notes — now in a dedicated embed field ("Rena-notes")
    # Option A format from renabot.post_threat_card:
    #   embed.add_field(name="Rena-notes", value=note, inline=False)
    for field in embed.fields:
        name = (field.name or "").strip().lower()
        if name.replace(" ", "") in {"rena-notes", "renanotes"}:
            if field.value:
                data["rena_notes"] = str(field.value).strip()
            break

    print(f"[SHADOWWATCHER] Parsed CVE: {cve_id} | CVSS: {data.get('cvss')} | Exploited: {data.get('exploited')}")
    return data

# =========================
# Shadow Watcher: llama.cpp Client
# =========================
class LlamaCppClient:
    """llama.cpp HTTP server client for Shadow Watcher analysis"""
    
    def __init__(self):
        self.base_url = LLAMACPP_BASE_URL
        self.model = LLAMACPP_MODEL
        self.max_tokens = LLAMACPP_MAX_TOKENS
        self.temperature = LLAMACPP_TEMP
        self.timeout = LLAMACPP_TIMEOUT
        self.enabled = LLAMACPP_ENABLED
    
    async def chat_completion(self, system_prompt: str, user_prompt: str,
                             temperature: Optional[float] = None,
                             max_tokens: Optional[int] = None) -> Optional[str]:
        """Generate chat completion via llama.cpp server"""
        if not self.enabled:
            print("[LLAMACPP] Disabled in config")
            return None
        
        try:
            payload = {
                "prompt": f"<|system|>{system_prompt}<|end|><|user|>{user_prompt}<|end|><|assistant|>",
                "temperature": temperature if temperature is not None else self.temperature,
                "n_predict": max_tokens if max_tokens is not None else self.max_tokens,
                "stop": ["<|end|>", "</s>"],
                "cache_prompt": True,
            }
            
            url = f"{self.base_url}/completion"
            print(f"[LLAMACPP] Requesting completion from {url}...")
            
            # Make async request
            loop = asyncio.get_event_loop()
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda: requests.post(url, json=payload, timeout=self.timeout)
                ),
                timeout=self.timeout + 5
            )
            
            if response.status_code == 200:
                result = response.json()
                content = result.get("content", "").strip()
                print(f"[LLAMACPP] Got {len(content)} chars")
                return content or None
            else:
                print(f"[LLAMACPP] Error {response.status_code}: {response.text[:200]}")
                return None
                
        except asyncio.TimeoutError:
            print(f"[LLAMACPP] Timeout after {self.timeout}s")
            return None
        except Exception as e:
            print(f"[LLAMACPP] Error: {e}")
            return None

# Global llama.cpp client
llamacpp_client = LlamaCppClient()

# =========================
# Shadow Watcher: Threat Analyzer
# =========================
async def analyze_cve_threat(cve_data: Dict[str, Any], nvd_data: Optional[Dict] = None) -> Optional[str]:
    """Generate Astrae's threat assessment using llama.cpp (OPTIMIZED FOR SPEED & QUEUED)"""
    try:
        # Build context - Concise version to prevent timeouts
        context_parts = [
            f"ID: {cve_data['cve_id']}",
            f"Rena Note: {short(cve_data.get('rena_notes', 'Threat identified'), 100)}"
        ]
        
        if cve_data.get("cvss"):
            cvss = cve_data["cvss"]
            context_parts.append(f"CVSS: {cvss['severity']} ({cvss['score']})")
        
        # Add NVD data if available (Heavily truncated)
        if nvd_data and "vulnerabilities" in nvd_data:
            try:
                vuln = nvd_data["vulnerabilities"][0]
                cve = vuln.get("cve", {})
                
                # Description - Truncated to 150 chars to save tokens
                descriptions = cve.get("descriptions", [])
                if descriptions:
                    desc = descriptions[0].get("value", "")
                    if desc:
                        context_parts.append(f"Desc: {desc[:150]}...")
                
                # Metrics (Simplified)
                metrics = cve.get("metrics", {})
                if "cvssMetricV31" in metrics:
                    cvss_v3 = metrics["cvssMetricV31"][0]["cvssData"]
                    context_parts.append(f"Vector: {cvss_v3.get('attackVector', 'N/A')}")
                
            except (KeyError, IndexError) as e:
                print(f"[SHADOWWATCHER] Error parsing NVD data: {e}")
        
        # Build full prompt
        context_str = "\n".join(context_parts)
        
        # We inject the infrastructure context, but we trust the system prompt to know what CHIRASU is
        # to save input tokens.
        user_prompt = f"""Analyze this CVE:
{context_str}

Infrastructure: Headscale VPN, Keycloak, SSH Hardened, IP Cams, Blockchain Nodes.

Assess."""
        
        # Call llama.cpp with strict limits
        # [CRITICAL FIX] Use semaphore to queue requests. 
        # This prevents 5 concurrent CVEs from timing out simultaneously.
        async with LLM_SEMAPHORE:
            response = await llamacpp_client.chat_completion(
                system_prompt=SHADOWWATCHER_SYSTEM,
                user_prompt=user_prompt,
                temperature=0.3, # Lower temp for faster, more deterministic output
                max_tokens=128   # Reduced from 280 to prevent timeout
            )
        
        if response:
            # Clean up response
            response = response.strip()
            # Ensure it starts with "Assessment validity"
            if "Assessment validity:" not in response:
                # Fallback format if model hallucinates
                return f"Assessment validity: UNCERTAIN\nConfidence: 50%\nImpact: UNKNOWN\n---\n{short(response, 100)}"
            
            # Formatting clean up (remove markdown blocks if model added them)
            response = response.replace("```", "").strip()
            return response
        
        return None
        
    except Exception as e:
        print(f"[SHADOWWATCHER] Analysis error: {e}")
        traceback.print_exc()
        return None

# =========================
# LLM Clients with Async + Semaphore (Ollama / primary + llama.cpp chat)
# =========================
EXCEPTIONS_RING = deque(maxlen=64)

async def _post_json_async(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int = 90) -> Dict[str, Any]:
    """Async wrapper for requests to avoid blocking event loop"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: requests.post(url, headers=headers, json=payload, timeout=timeout).json()
    )

async def llm_chat(system_prompt: str, user_text: str, model: Optional[str] = None,
                   temperature: Optional[float] = None, max_tokens: int = 150) -> Optional[str]:
    """Chat completion with semaphore-based concurrency control"""
    # First preference: llama.cpp for Astrae's chat/persona if enabled
    if USE_LLAMACPP_FOR_CHAT and LLAMACPP_ENABLED:
        async with LLM_SEMAPHORE:
            try:
                print(f"[LLM] (llama.cpp) Chat call: {len(user_text)} chars...")
                response = await llamacpp_client.chat_completion(
                    system_prompt=system_prompt,
                    user_prompt=user_text,
                    temperature=temperature if temperature is not None else None,
                    max_tokens=max_tokens,
                )
                if response:
                    return response.strip()
                print("[LLM] (llama.cpp) Empty response, falling back to primary LLM provider")
            except Exception as e:
                print(f"[LLM] (llama.cpp) ERROR: {type(e).__name__}: {e}")
                EXCEPTIONS_RING.append((datetime.now(), "llm_chat_llamacpp", str(e)))
                # fall through to primary provider

    # Fallback / primary provider (Ollama or OpenAI-compatible)
    if not LLM_BASE_URL:
        print("[LLM] No base URL configured for primary provider")
        return None
    
    # PERFORMANCE: Use semaphore to limit concurrent calls
    async with LLM_SEMAPHORE:
        try:
            print(f"[LLM] Chat call: {len(user_text)} chars...")
            if LLM_PROVIDER == "ollama":
                url = LLM_BASE_URL
                if url.endswith("/v1"):
                    url = url[:-3]
                payload = {
                    "model": model or LLM_MODEL,
                    "keep_alive": "10m",  # Keep model in memory for 10 minutes
                    "options": {
                        "temperature": LLM_TEMP if temperature is None else temperature,
                        "num_ctx": 1024,
                        "num_predict": max_tokens,
                    },
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                }
                print(f"[LLM] POST to {url}/api/chat (timeout: {LLM_TIMEOUT}s, first call may be slower)")
                data = await asyncio.wait_for(
                    _post_json_async(url + "/api/chat", {"Content-Type": "application/json"}, payload, timeout=LLM_TIMEOUT),
                    timeout=LLM_TIMEOUT + 5
                )
                print(f"[LLM] Response received")
                if "message" in data and isinstance(data["message"], dict):
                    return (data["message"].get("content") or "").strip() or None
                if "messages" in data and data["messages"]:
                    return (data["messages"][-1].get("content") or "").strip() or None
                print("[LLM] Unexpected response format")
                return None
            else:
                # OpenAI-compatible API
                payload = {
                    "model": model or LLM_MODEL,
                    "temperature": LLM_TEMP if temperature is None else temperature,
                    "max_tokens": max_tokens,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_text},
                    ],
                }
                data = await asyncio.wait_for(
                    _post_json_async(
                        f"{LLM_BASE_URL}/chat/completions",
                        {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                        payload, timeout=LLM_TIMEOUT
                    ),
                    timeout=LLM_TIMEOUT + 5
                )
                return (data["choices"][0]["message"]["content"] or "").strip()
        except asyncio.TimeoutError:
            print(f"[LLM] TIMEOUT after {LLM_TIMEOUT}s - model may still be loading")
            EXCEPTIONS_RING.append((datetime.now(), "llm_chat_timeout", f"Timeout after {LLM_TIMEOUT}s"))
            return None
        except Exception as e:
            print(f"[LLM] ERROR: {type(e).__name__}: {e}")
            EXCEPTIONS_RING.append((datetime.now(), "llm_chat", str(e)))
            return None

async def llm_vision_describe(prompt: str, image_urls: List[str], model: Optional[str] = None,
                               temperature: Optional[float] = None, max_tokens: int = 120) -> Optional[str]:
    """Vision completion using dedicated vision backend (Aoi) with fallback to primary if unset."""
    if not image_urls:
        return None
    
    # Use dedicated vision backend if configured, otherwise fall back to primary LLM
    vision_base = VISION_BASE_URL if VISION_BASE_URL else LLM_BASE_URL
    vision_provider = VISION_PROVIDER if VISION_PROVIDER else LLM_PROVIDER
    vision_model = model or (VISION_MODEL if VISION_BASE_URL else LLM_VISION_MODEL)
    timeout = VISION_TIMEOUT if VISION_BASE_URL else LLM_TIMEOUT
    
    if not vision_base:
        return None
    
    # PERFORMANCE: Use semaphore to limit concurrent calls
    async with LLM_SEMAPHORE:
        try:
            print(f"[VISION] Processing {len(image_urls)} image(s) via {vision_provider} @ {vision_base}...")
            if vision_provider == "ollama":
                url = vision_base
                if url.endswith("/v1"):
                    url = url[:-3]
                payload = {
                    "model": vision_model,
                    "options": {
                        "temperature": LLM_TEMP if temperature is None else temperature,
                        "num_ctx": 1024,
                        "num_predict": max_tokens,
                    },
                    "messages": [
                        {"role": "user", "content": prompt, "images": image_urls[:3]}
                    ],
                }
                data = await asyncio.wait_for(
                    _post_json_async(url + "/api/chat", {"Content-Type": "application/json"}, payload, timeout=timeout),
                    timeout=timeout + 5
                )
                if "message" in data and isinstance(data["message"], dict):
                    return (data["message"].get("content") or "").strip() or None
                if "messages" in data and data["messages"]:
                    return (data["messages"][-1].get("content") or "").strip() or None
                return None
            else:
                # OpenAI-compatible multimodal fallback
                content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
                for u in image_urls[:3]:
                    content.append({"type": "image_url", "image_url": {"url": u}})
                payload = {
                    "model": vision_model,
                    "temperature": LLM_TEMP if temperature is None else temperature,
                    "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": content}],
                }
                data = await asyncio.wait_for(
                    _post_json_async(
                        f"{vision_base}/chat/completions",
                        {"Authorization": f"Bearer {LLM_API_KEY}", "Content-Type": "application/json"},
                        payload, timeout=timeout
                    ),
                    timeout=timeout + 5
                )
                return (data["choices"][0]["message"]["content"] or "").strip()
        except asyncio.TimeoutError:
            print(f"[VISION] Timeout after {timeout}s")
            EXCEPTIONS_RING.append((datetime.now(), "vision_timeout", f"Timeout after {timeout}s"))
            return None
        except Exception as e:
            print(f"[VISION] Error: {e}")
            EXCEPTIONS_RING.append((datetime.now(), "llm_vision_describe", str(e)))
            return None

# =========================
# Text Analysis Brain
# =========================
async def analyze_owner_prompt(text: str) -> str:
    """Quick analysis for simple owner commands"""
    for p in ("astrae,", "astrae:", "astrae;", "astrae "):
        if text.startswith(p):
            text = text[len(p):].strip()
            break
    if not text:
        return "Parameters missing. Restate intent."
    
    t = text.lower()
    
    # Only hardcode true diagnostic commands
    if t in ("uptime", "system uptime", "how long"):
        return f"Host uptime: {human_uptime()}. Systems nominal."
    if t in ("status", "system status", "diagnostics"):
        return (
            f"Node operational. {hostname_brief()} · {platform_brief()} · "
            f"Uptime {human_uptime()} · Load {loadavg_brief()} · Mem {mem_brief()}. "
            f"Confidence 97%."
        )
    
    # Everything else (including greetings, questions, chat) goes to LLM for personality
    reply = await llm_chat(ASTRAE_CORE_IDENTITY, text, max_tokens=280)
    if reply:
        return reply
    
    # Only show this fallback if LLM completely fails
    return "Query processed. Response confidence... insufficient. Connection variance detected."

async def memes_personalize(caption: str, user_text: str) -> str:
    """Generate personalized meme log entry in Astrae's voice"""
    context = f"IMAGE VISUAL DATA:\n{short(caption, 400)}\n"
    if user_text:
        context += f"POSTER COMMENTARY:\n{short(user_text, 200)}\n"
    
    prompt = f"{context}\nGenerate sensor log entry (2-4 lines, under 220 chars, dry technical humor, secretly charmed):"
    
    out = await llm_chat(ASTRAE_MEME_PERSONA, prompt, model=LLM_MODEL, temperature=0.7, max_tokens=80)
    return (out or "").strip()

# =========================
# Discord Bot
# =========================
class AstraeClient(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = False
        super().__init__(intents=intents)
        
        self.tree = app_commands.CommandTree(self)
        self.boot_ts = time.time()
        
        # Chat settings
        self.chat_enabled = True
        self.allowed_channels: Set[int] = ALLOWED_CHANNEL_IDS.copy()
        self.meme_channels: Set[int] = MEMES_CHANNEL_IDS.copy()
        
        # Rate limiting for memes
        self.post_min_interval_sec = 120
        self.last_post_time: Dict[int, float] = {}
        
        # Shadow Watcher state
        self.shadowwatcher_enabled = SHADOWWATCHER_ENABLED
        self.shadowwatcher_channel = SHADOWWATCHER_CHANNEL_ID
        self.rena_bot_id = RENA_BOT_ID
        self.processing_cves: Set[str] = set()  # Track CVEs being processed

    async def setup_hook(self):
        """Called once on startup - setup slash commands"""
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"[COMMANDS] Synced {len(synced)} commands to guild {GUILD_ID}")
        else:
            synced = await self.tree.sync()
            print(f"[COMMANDS] Synced {len(synced)} commands globally")

    async def on_ready(self):
        print(f"[READY] {BOT_NAME} v{VERSION}")
        print(f"[READY] Logged in as {self.user} (ID: {self.user.id})")
        print(f"[READY] Connected to {len(self.guilds)} guild(s)")
        print(f"[READY] Chat enabled: {self.chat_enabled}")
        print(f"[READY] Allowed channels: {sorted(self.allowed_channels)}")
        print(f"[READY] Meme channels: {sorted(self.meme_channels)}")
        print(f"[READY] Shadow Watcher: {'ENABLED' if self.shadowwatcher_enabled else 'DISABLED'}")
        if self.shadowwatcher_enabled:
            print(f"[READY] Shadow Watcher channel: {self.shadowwatcher_channel}")
            print(f"[READY] Rena bot ID: {self.rena_bot_id}")
            print(f"[READY] llama.cpp: {'ENABLED' if LLAMACPP_ENABLED else 'DISABLED'}")
        
        # Log vision configuration
        if VISION_BASE_URL:
            print(f"[READY] Vision backend: {VISION_PROVIDER} @ {VISION_BASE_URL} | Model: {VISION_MODEL}")
        else:
            print(f"[READY] Vision: Using primary LLM backend")
        
        # Start presence rotation
        if not self.presence_pulse.is_running():
            self.presence_pulse.start()

    async def on_ready(self):
        print(f"[READY] {BOT_NAME} v{VERSION}")
        print(f"[READY] Logged in as {self.user} (ID: {self.user.id})")
        print(f"[READY] Connected to {len(self.guilds)} guild(s)")
        print(f"[READY] Chat enabled: {self.chat_enabled}")
        print(f"[READY] Allowed channels: {sorted(self.allowed_channels)}")
        print(f"[READY] Meme channels: {sorted(self.meme_channels)}")
        print(f"[READY] Shadow Watcher: {'ENABLED' if self.shadowwatcher_enabled else 'DISABLED'}")
        if self.shadowwatcher_enabled:
            print(f"[READY] Shadow Watcher channel: {self.shadowwatcher_channel}")
            print(f"[READY] Rena bot ID: {self.rena_bot_id}")
            print(f"[READY] llama.cpp: {'ENABLED' if LLAMACPP_ENABLED else 'DISABLED'}")
        
        # Log vision configuration
        if VISION_BASE_URL:
            print(f"[READY] Vision backend: {VISION_PROVIDER} @ {VISION_BASE_URL} | Model: {VISION_MODEL}")
        else:
            print(f"[READY] Vision: Using primary LLM backend")
        
        # Start presence rotation
        if not self.presence_pulse.is_running():
            self.presence_pulse.start()

    async def on_message(self, message: discord.Message):
        # Ignore Astrae's own messages
        if message.author == self.user:
            return

        # =========================
        # Shadow Watcher: Rena → Astrae handoff
        # =========================
        try:
            if self.shadowwatcher_enabled and message.channel.id == self.shadowwatcher_channel:
                # Debug so we can see what Astrae is actually receiving
                print(
                    f"[SHADOWWATCHER:DEBUG] incoming | "
                    f"author={message.author} "
                    f"(id={message.author.id}, bot={message.author.bot}) | "
                    f"embeds={len(message.embeds)} | "
                    f"reference={bool(message.reference)}"
                )

                # Only care about fresh, embedded threat cards
                if not message.reference and message.embeds:
                    # If RENA_BOT_ID is configured (>0), be strict and only trust Rena
                    if self.rena_bot_id and self.rena_bot_id > 0:
                        if message.author.bot and message.author.id == self.rena_bot_id:
                            await self._handle_shadowwatcher(message)
                            return
                    else:
                        # Fallback mode: no RENA_BOT_ID set correctly
                        # → accept any BOT posting embeds in this channel
                        if message.author.bot:
                            print(
                                "[SHADOWWATCHER:DEBUG] RENA_BOT_ID not set or 0; "
                                "accepting any bot embed in ShadowWatcher channel."
                            )
                            await self._handle_shadowwatcher(message)
                            return
        except Exception as e:
            EXCEPTIONS_RING.append((datetime.now(), "on_message_shadowwatcher", str(e)))

        # =========================
        # Normal bot / chat logic
        # =========================

        # Ignore other bots for normal paths (we already handled SW above)
        if message.author.bot:
            return

        try:
            # Meme watcher (any user, in specific channels)
            if message.channel.id in self.meme_channels:
                await self._maybe_handle_meme(message)

            # Owner-only interactions for chat
            if message.author.id != OWNER_ID:
                return

            # Allowed channel gate (unless direct mention)
            is_mentioned = self.user and self.user.mentioned_in(message)
            if message.guild and self.allowed_channels:
                if (message.channel.id not in self.allowed_channels) and not is_mentioned:
                    return

            if not self.chat_enabled:
                return

            content = (message.content or "").strip()
            if not content:
                return

            # Invocation: name prefix or @mention
            lower = content.lower()
            invoked = False
            name_trigs = ("astrae,", "astrae:", "astrae;", "astrae ")

            # Check for name-based invocation
            if any(lower.startswith(t) for t in name_trigs):
                invoked = True
            # Check for @mention
            elif is_mentioned:
                invoked = True
                # Remove the mention from content
                if self.user:
                    content = content.replace(f'<@{self.user.id}>', '').replace(f'<@!{self.user.id}>', '')

            if not invoked:
                return

            await self._handle_owner_chat(message, content)

        except Exception as e:
            EXCEPTIONS_RING.append((datetime.now(), "on_message", str(e)))


        # =========================
        # Normal bot / chat logic
        # =========================

        # Ignore other bots for normal paths (we already handled SW above)
        if message.author.bot:
            return

        try:
            # Meme watcher (any user, in specific channels)
            if message.channel.id in self.meme_channels:
                await self._maybe_handle_meme(message)

            # Owner-only interactions for chat
            if message.author.id != OWNER_ID:
                return

            # Allowed channel gate (unless direct mention)
            is_mentioned = self.user and self.user.mentioned_in(message)
            if message.guild and self.allowed_channels:
                if (message.channel.id not in self.allowed_channels) and not is_mentioned:
                    return

            if not self.chat_enabled:
                return

            content = (message.content or "").strip()
            if not content:
                return

            # Invocation: name prefix or @mention
            lower = content.lower()
            invoked = False
            name_trigs = ("astrae,", "astrae:", "astrae;", "astrae ")

            # Check for name-based invocation
            if any(lower.startswith(t) for t in name_trigs):
                invoked = True
            # Check for @mention
            elif is_mentioned:
                invoked = True
                # Remove the mention from content
                if self.user:
                    content = content.replace(f'<@{self.user.id}>', '').replace(f'<@!{self.user.id}>', '')

            if not invoked:
                return

            await self._handle_owner_chat(message, content)

        except Exception as e:
            EXCEPTIONS_RING.append((datetime.now(), "on_message", str(e)))


    async def _handle_shadowwatcher(self, message: discord.Message):
        """Handle Rena's CVE threat card"""
        try:
            print(f"[SHADOWWATCHER] Detected potential CVE post from Rena")
            
            # Parse the embed
            cve_data = parse_rena_cve_embed(message)
            if not cve_data:
                # Silently skip if no CVE data found (prevents log noise on non-threat posts)
                print(f"[SHADOWWATCHER] Skipped message (No CVE data found): '{message.content[:50]}...'")
                return
            
            cve_id = cve_data["cve_id"]
            
            # Prevent duplicate processing
            if cve_id in self.processing_cves:
                print(f"[SHADOWWATCHER] Already processing {cve_id}, skipping")
                return
            
            self.processing_cves.add(cve_id)
            
            try:
                # Show typing indicator
                async with message.channel.typing():
                    print(f"[SHADOWWATCHER] Analyzing {cve_id}...")
                    
                    # Fetch full CVE data from NVD (optional, adds detail)
                    nvd_data = await nvd_client.fetch_cve(cve_id)
                    
                    # Generate Astrae's assessment
                    assessment = await analyze_cve_threat(cve_data, nvd_data)
                    
                    if assessment:
                        # Format response
                        response = f"**Shadow Watcher Analysis — {cve_id}**\n```\n{assessment}\n```\n"
                        response += f"*Source: Rena — ShadowWatcher · NVD database correlation · llama.cpp inference*"
                        
                        # Post as NEW message (not a reply)
                        await message.channel.send(response)
                        print(f"[SHADOWWATCHER] Posted assessment for {cve_id}")
                    else:
                        # Fallback response
                        fallback = (
                            f"**Shadow Watcher Analysis — {cve_id}**\n```\n"
                            f"Assessment validity: INSUFFICIENT\n"
                            f"Confidence: 47%\n"
                            f"Impact: UNKNOWN\n"
                            f"---\n"
                            f"Analysis pipeline encountered variance. LLM inference unavailable.\n"
                            f"Manual review recommended. Pattern recognition... incomplete.\n"
                            f"Emotional response logged as: frustration. Query Void-sama for assistance.\n"
                            f"```"
                        )
                        await message.channel.send(fallback)
                        print(f"[SHADOWWATCHER] Posted fallback for {cve_id}")
            
            finally:
                # Remove from processing set
                self.processing_cves.discard(cve_id)
                
        except Exception as e:
            print(f"[SHADOWWATCHER] Error: {e}")
            traceback.print_exc()
            EXCEPTIONS_RING.append((datetime.now(), "shadowwatcher", str(e)))

    async def _maybe_handle_meme(self, message: discord.Message):
        # Rate limit per-channel
        now = time.time()
        if now - self.last_post_time.get(message.channel.id, 0) < self.post_min_interval_sec:
            return

        imgs = image_urls_from_message(message)
        if not imgs:
            return

        # Show typing indicator
        async with message.channel.typing():
            prompt = "Describe the image(s) briefly. Focus on salient visual elements, subjects, composition, mood."
            try:
                caption = await llm_vision_describe(prompt, imgs) or ""
            except Exception as e:
                EXCEPTIONS_RING.append((datetime.now(), "meme_caption", str(e)))
                caption = ""

            if caption:
                whisper = await memes_personalize(caption, message.content or "")
                if whisper:
                    self.last_post_time[message.channel.id] = now
                    out = f"(logged)\n{short(whisper, 220)}"
                    try:
                        await message.channel.send(out)
                    except Exception as e:
                        EXCEPTIONS_RING.append((datetime.now(), "meme_send", str(e)))

    async def _handle_owner_chat(self, message: discord.Message, content: str):
        # Show typing indicator
        async with message.channel.typing():
            print(f"[CHAT] Processing message from {message.author}: {content[:50]}...")
            
            # Check for image attachments
            imgs = image_urls_from_message(message)
            
            if imgs and VISION_BASE_URL:
                # Process image with vision
                print(f"[CHAT] Found {len(imgs)} image(s), processing with vision...")
                vision_result = await llm_vision_describe(
                    "Analyze this image: describe subjects, composition, notable elements, mood, technical details.",
                    imgs,
                    max_tokens=150
                )
                
                if vision_result:
                    # Generate Astrae's response to the vision analysis
                    vision_prompt = f"""Visual scan complete. Raw data:
{short(vision_result, 400)}

User's query: {content}

Respond as Astrae: clinical precision, measurements where applicable, subtle dry humor through over-analysis.
2-3 sentences maximum. Reference the visual data analytically. Pattern recognition nominal."""
                    
                    reply = await llm_chat(
                        ASTRAE_CORE_IDENTITY,
                        vision_prompt,
                        temperature=0.7,
                        max_tokens=150
                    )
                    
                    if reply:
                        print(f"[CHAT] Vision-based reply generated")
                    else:
                        # Fallback if personality generation fails
                        reply = f"Visual analysis complete. Confidence: 86.7%.\n{short(vision_result, 300)}"
                else:
                    print(f"[CHAT] Vision analysis failed, falling back to text response")
                    reply = await analyze_owner_prompt(content.lower())
            else:
                # Regular text response
                reply = await analyze_owner_prompt(content.lower())
            
            print(f"[CHAT] Got reply: {reply[:100] if reply else 'None'}...")
            
            # If LLM failed, provide a diagnostic response
            if not reply:
                mem = psutil.virtual_memory()
                reply = f"Processing error detected. System load: {mem.percent}% | Check logs for diagnostics."
                print(f"[CHAT] LLM returned None, using fallback")
            
            try:
                await message.reply(short(reply, 1200), mention_author=False)
                print("[CHAT] Reply sent successfully")
            except Exception as e:
                print(f"[CHAT] Failed to send reply: {e}")
                EXCEPTIONS_RING.append((datetime.now(), "send_reply", str(e)))

    @tasks.loop(seconds=600)
    async def presence_pulse(self):
        try:
            activity = discord.Activity(
                type=discord.ActivityType.watching,
                name=random.choice(ASTRAE_PRESENCES)
            )
            await self.change_presence(activity=activity, status=discord.Status.idle)
            # Jitter next interval (8–12 min)
            self.presence_pulse.change_interval(seconds=random.randint(480, 720))
        except Exception as e:
            EXCEPTIONS_RING.append((datetime.now(), "presence_pulse", str(e)))

# =========================
# Slash Commands
# =========================
bot = AstraeClient()

@bot.tree.command(name="ping", description="Check if Astrae is responding.")
async def ping(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000, 1)
    await interaction.response.send_message(
        f"Acknowledged. Latency: {latency_ms}ms · Uptime: {uptime_str(bot.boot_ts)} · Confidence ≥ 93%",
        ephemeral=True
    )

@bot.tree.command(name="health", description="Model health / quick check.")
async def health(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True, thinking=True)
    
    # Chat check
    chat_ok = False
    chat_excerpt = None
    try:
        chat_excerpt = await llm_chat("You are a terse health checker.", "Return the word READY.", max_tokens=8)
        chat_ok = (chat_excerpt is not None) and ("READY" in chat_excerpt.upper())
    except Exception as e:
        EXCEPTIONS_RING.append((datetime.now(), "health_chat", str(e)))
    
    # llama.cpp check (if enabled)
    llamacpp_ok = False
    if LLAMACPP_ENABLED:
        try:
            test_response = await llamacpp_client.chat_completion(
                "You are a health checker.",
                "Reply with: OPERATIONAL",
                max_tokens=8
            )
            llamacpp_ok = test_response and "OPERATIONAL" in test_response.upper()
        except Exception as e:
            EXCEPTIONS_RING.append((datetime.now(), "health_llamacpp", str(e)))
    
    # System info
    host = hostname_brief()
    load = loadavg_brief()
    mem = psutil.virtual_memory()
    proc = psutil.Process()
    rss = proc.memory_info().rss / (1024*1024)

    chat_backend = "llama.cpp" if USE_LLAMACPP_FOR_CHAT and LLAMACPP_ENABLED else LLM_PROVIDER
    
    msg = (
        f"**{BOT_NAME} v{VERSION}**\n"
        f"- Host: `{host}` · OS `{platform_brief()}`\n"
        f"- Bot uptime: `{uptime_str(bot.boot_ts)}` · System: `{human_uptime()}`\n"
        f"- Load: `{load}` · Mem: `{mem.percent}%` · Bot RSS: `{rss:.1f} MiB`\n"
        f"- LLM: `{LLM_PROVIDER}` @ `{LLM_BASE_URL}`\n"
        f"- Chat backend: `{chat_backend}`\n"
        f"- Model: `{LLM_MODEL}` · Vision: `{LLM_VISION_MODEL}`\n"
        f"- Chat OK: `{chat_ok}` · Echo: `{short(chat_excerpt or '—', 80)}`\n"
        f"- llama.cpp: `{'ENABLED' if LLAMACPP_ENABLED else 'DISABLED'}` · OK: `{llamacpp_ok if LLAMACPP_ENABLED else 'N/A'}`\n"
        f"- Shadow Watcher: `{'ENABLED' if bot.shadowwatcher_enabled else 'DISABLED'}`\n"
        f"- Timeout: `{LLM_TIMEOUT}s` (allows model loading) · Concurrent: `1` · Exceptions: `{len(EXCEPTIONS_RING)}`"
    )
    await interaction.followup.send(msg, ephemeral=True)

@bot.tree.command(name="uptime", description="Host uptime.")
async def uptime_cmd(interaction: discord.Interaction):
    await interaction.response.send_message(
        f"Host uptime: {human_uptime()}.",
        ephemeral=True
    )

@bot.tree.command(name="status", description="Process stats.")
async def status_cmd(interaction: discord.Interaction):
    proc = psutil.Process()
    rss = proc.memory_info().rss / (1024*1024)
    msg = (
        f"Node: {hostname_brief()} | OS: {platform_brief()}\n"
        f"Uptime: {human_uptime()} | Load: {loadavg_brief()}\n"
        f"Mem: {mem_brief()} | Bot RSS: {rss:.1f} MiB\n"
        f"Exceptions: {len(EXCEPTIONS_RING)}"
    )
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="whereami", description="Show guild/channel IDs.")
async def whereami_cmd(interaction: discord.Interaction):
    g = interaction.guild
    ch = interaction.channel
    msg = (
        f"Guild: {g.name if g else 'DM'} ({g.id if g else 'n/a'})\n"
        f"Channel: {getattr(ch, 'name', 'DM')} ({getattr(ch, 'id', 'n/a')})"
    )
    await interaction.response.send_message(msg, ephemeral=True)

@bot.tree.command(name="toggle_chat", description="Enable/disable Astrae chat (owner only).")
@app_commands.describe(enable="True to enable, False to disable")
async def toggle_chat(interaction: discord.Interaction, enable: bool):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("Owner only.", ephemeral=True)
        return
    bot.chat_enabled = enable
    await interaction.response.send_message(f"Chat enabled = `{bot.chat_enabled}`", ephemeral=True)

@bot.tree.command(name="allow_channel", description="Add this channel to allowed chat channels (owner only).")
async def allow_channel(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("Owner only.", ephemeral=True)
        return
    bot.allowed_channels.add(interaction.channel_id)
    await interaction.response.send_message(
        f"Added `{interaction.channel_id}` to allowed chat channels.\n"
        f"Current: {sorted(bot.allowed_channels)}",
        ephemeral=True
    )

@bot.tree.command(name="set_allowed_channels", description="Set allowed chat channel IDs (owner only).")
@app_commands.describe(csv_ids="Comma-separated IDs, e.g. 123,456,789")
async def set_allowed_channels(interaction: discord.Interaction, csv_ids: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("Owner only.", ephemeral=True)
        return
    ids = {int(x) for x in csv_ids.replace(" ", "").split(",") if x.isdigit()}
    bot.allowed_channels = set(ids)
    await interaction.response.send_message(
        f"Allowed chat channels set to: {sorted(bot.allowed_channels)}",
        ephemeral=True
    )

@bot.tree.command(name="set_meme_channels", description="Set meme-observer channel IDs (owner only).")
@app_commands.describe(csv_ids="Comma-separated IDs, e.g. 123,456")
async def set_meme_channels(interaction: discord.Interaction, csv_ids: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("Owner only.", ephemeral=True)
        return
    ids = {int(x) for x in csv_ids.replace(" ", "").split(",") if x.isdigit()}
    bot.meme_channels = set(ids)
    await interaction.response.send_message(
        f"Meme channels set to: {sorted(bot.meme_channels)}",
        ephemeral=True
    )

@bot.tree.command(name="analyze", description="Analyze an image (attach one or scan recent history).")
@app_commands.describe(image="Optional image to analyze")
async def analyze_cmd(interaction: discord.Interaction, image: Optional[discord.Attachment] = None):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("Access restricted. Owner authorization required.", ephemeral=True)
        return

    # Defer with thinking indicator
    await interaction.response.defer(thinking=True, ephemeral=False)

    image_urls: List[str] = []
    try:
        if image is not None:
            ct = (image.content_type or "").lower()
            fn = (image.filename or "").lower()
            if ("image/" in ct) or fn.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                image_urls.append(image.url)
    except Exception:
        pass

    if not image_urls and interaction.channel and isinstance(interaction.channel, discord.TextChannel):
        image_urls = await _recent_image_urls(interaction.channel, limit=30)

    if not image_urls:
        return await interaction.followup.send(
            "No image found in scan range. Visual data required for analysis.",
            ephemeral=False
        )

    # Get detailed vision analysis (primary LLM / Ollama)
    caption = await llm_vision_describe(
        "Provide detailed analysis: subjects, composition, colors, mood, context, notable elements.",
        image_urls,
        max_tokens=180
    ) or ""
    
    # Generate personality response in Astrae's canonical tone
    analysis_prompt = f"""Visual analysis results:
{short(caption, 500)}

Provide your analytical assessment as Astrae (O1-005): 2-4 sentences, clinical and precise, quietly poetic.
Speak like a kuudere systems analyst whose deadpan over-precision sometimes reads as unintentionally funny.
Do not force jokes or emojis; keep the tone serious, data-driven, and emotionally restrained."""
    
    personality_response = await llm_chat(
        ASTRAE_CORE_IDENTITY,
        analysis_prompt,
        temperature=0.7,
        max_tokens=120
    )
    
    if personality_response:
        await interaction.followup.send(
            f"**Visual Analysis Report**\n{short(personality_response, 800)}",
            ephemeral=False
        )
    else:
        # Fallback
        await interaction.followup.send(
            f"Analysis complete. Visual data processed.\n\n{short(caption, 600)}",
            ephemeral=False
        )

@bot.tree.command(name="shadowwatcher", description="Toggle Shadow Watcher CVE analysis (owner only).")
@app_commands.describe(enable="True to enable, False to disable")
async def shadowwatcher_cmd(interaction: discord.Interaction, enable: bool):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("Owner only.", ephemeral=True)
        return
    bot.shadowwatcher_enabled = enable
    status = "ENABLED" if enable else "DISABLED"
    await interaction.response.send_message(
        f"Shadow Watcher CVE analysis: `{status}`",
        ephemeral=True
    )

@bot.tree.command(name="sync_now", description="Force re-sync of slash commands (owner only).")
async def sync_now_cmd(interaction: discord.Interaction):
    if interaction.user.id != OWNER_ID:
        return await interaction.response.send_message("Owner only.", ephemeral=True)
    try:
        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            synced = await bot.tree.sync(guild=guild)
        else:
            synced = await bot.tree.sync()
        await interaction.response.send_message(f"Synced {len(synced)} commands.", ephemeral=True)
    except Exception as e:
        await interaction.response.send_message(f"Sync error: {e}", ephemeral=True)

@bot.tree.command(name="list_cmds", description="List available slash commands.")
async def list_cmds_cmd(interaction: discord.Interaction):
    cmds = [c.name for c in bot.tree.get_commands()]
    await interaction.response.send_message(
        "Registered: " + ", ".join(sorted(cmds)),
        ephemeral=True
    )

# =========================
# Entrypoint
# =========================
def main():
    if not DISCORD_TOKEN:
        print("[FATAL] DISCORD_TOKEN missing - cannot start bot.")
        print("[FATAL] Ensure .env file exists in script directory with DISCORD_TOKEN=your_token")
        return 1
    
    try:
        print(f"[BOOT] {BOT_NAME} v{VERSION}")
        print(f"[BOOT] Working directory: {os.getcwd()}")
        print(f"[BOOT] Script location: {os.path.dirname(os.path.abspath(__file__))}")
        print(f"[LLM] Provider: {LLM_PROVIDER} | Base: {LLM_BASE_URL} | Model: {LLM_MODEL}")
        print(f"[LLAMACPP] Enabled: {LLAMACPP_ENABLED} | Base: {LLAMACPP_BASE_URL} | Model: {LLAMACPP_MODEL}")
        if VISION_BASE_URL:
            print(f"[VISION] Provider: {VISION_PROVIDER} | Base: {VISION_BASE_URL} | Model: {VISION_MODEL} | Timeout: {VISION_TIMEOUT}s")
        else:
            print(f"[VISION] Using primary LLM backend for vision")
        print(f"[PERF] Timeout: {LLM_TIMEOUT}s (allows model loading) | Concurrent limit: 1 (semaphore-controlled)")
        print(f"[SHADOWWATCHER] Enabled: {SHADOWWATCHER_ENABLED} | Channel: {SHADOWWATCHER_CHANNEL_ID} | Rena ID: {RENA_BOT_ID}")
        print(f"[CHAT] USE_LLAMACPP_FOR_CHAT = {USE_LLAMACPP_FOR_CHAT}")
        print("[DISCORD] Attempting to connect...")
        bot.run(DISCORD_TOKEN, log_handler=None)
        print("[DISCORD] Bot stopped normally")
    except Exception as e:
        print("[FATAL] run error:", e)
        traceback.print_exc()
        return 1
    return 0

if __name__ == "__main__":
    exit_code = main()
    import sys
    sys.exit(exit_code)
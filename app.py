import os
import re
import time
import math
import json
import asyncio
from typing import Dict, Any, List, Optional

import aiohttp
import discord
from discord.ext import commands, tasks
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn
from dotenv import load_dotenv

# =========================
# ENV / CONFIG
# =========================
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

# Posting cadence
POST_EVERY_SECONDS = int(os.getenv("POST_EVERY_SECONDS", "60"))

# DexScreener endpoints
DEX_SEARCH_URL = "https://api.dexscreener.com/latest/dex/search"
DEX_LATEST_PAIRS = "https://api.dexscreener.com/latest/dex/pairs"

# RugCheck endpoint (simple verdict)
RUGCHECK_TOKEN_URL = "https://api.rugcheck.xyz/v1/tokens/{mint}"

# Trend keywords (override with comma-separated KEYWORDS in .env)
DEFAULT_KEYWORDS = [
    "pepe","doge","shib","inu","cat","kitty","wojak","elon","trump","420",
    "moon","pump","based","turbo","toad","frog","bonk","meme","wagmi","lambo",
    "ponke","pippin","giga","rekt","gme","wallstreet","corgi","pudgy","jito"
]
KEYWORDS = [k.strip() for k in os.getenv("KEYWORDS", ",".join(DEFAULT_KEYWORDS)).split(",") if k.strip()]

# Heuristic thresholds (tune in .env)
MAX_TOKEN_AGE_MINUTES = int(os.getenv("MAX_TOKEN_AGE_MINUTES", "360"))   # <= 6h old
MAX_FDV = float(os.getenv("MAX_FDV", "8000000"))                         # <= $8m
MAX_LIQ_USD = float(os.getenv("MAX_LIQ_USD", "300000"))                  # <= $300k (avoid too-established)
MIN_LIQ_USD = float(os.getenv("MIN_LIQ_USD", "1000"))                    # >= $1k (avoid zero-liq)
MIN_BUY_SELL_RATIO_5M = float(os.getenv("MIN_BUY_SELL_RATIO_5M", "1.5")) # > 1.5 buys:sells last 5m
MIN_TXNS_5M = int(os.getenv("MIN_TXNS_5M", "20"))                        # >= 20 txns last 5m
MIN_VOLUME_5M = float(os.getenv("MIN_VOLUME_5M", "2000"))                # >= $2k last 5m
MIN_HOLDERS_HINT = int(os.getenv("MIN_HOLDERS_HINT", "0"))               # optional (DexScreener may not have)
SCORE_THRESHOLD = float(os.getenv("SCORE_THRESHOLD", "40"))              # overall score gate

# Discord intents (enable privileged intents in dev portal if you set True)
intents = discord.Intents.default()
intents.message_content = True   # turn on in portal if using text commands
intents.members = False
intents.presences = False
bot = commands.Bot(command_prefix="!", intents=intents)

# FastAPI app
app = FastAPI(title="MemeScanner")

# Shared HTTP session
_http: Optional[aiohttp.ClientSession] = None

# =========================
# UTIL
# =========================
MEME_RE = re.compile("|".join(re.escape(k) for k in KEYWORDS), re.I)

def now_ms() -> int:
    return int(time.time() * 1000)

def ms_to_ago_str(ms: int) -> str:
    delta_s = max(1, (now_ms() - ms) // 1000)
    units = [("d", 86400), ("h", 3600), ("m", 60), ("s", 1)]
    out = []
    for label, secs in units:
        if delta_s >= secs:
            val = delta_s // secs
            delta_s -= val * secs
            out.append(f"{val}{label}")
        if len(out) == 2:
            break
    return " ".join(out) + " ago"

def link_bundle(mint: str) -> Dict[str, str]:
    return {
        "dexscreener": f"https://dexscreener.com/solana/{mint}",
        "pumpfun": f"https://www.pump.fun/coin/{mint}",
        "axiom": f"https://axiom.trade/pulse?search={mint}",
        "dexscreener_search": f"https://dexscreener.com/solana?q={mint}"
    }

def safe_get(d: Dict, *path, default=None):
    cur = d
    for p in path:
        if cur is None:
            return default
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur if cur is not None else default

# =========================
# CORE: Fetch & Filter
# =========================
async def rugcheck_ok(mint: str) -> bool:
    """Return True if RugCheck verdict is not obviously malicious/honeypot."""
    global _http
    try:
        url = RUGCHECK_TOKEN_URL.format(mint=mint)
        async with _http.get(url, timeout=10) as r:
            if r.status != 200:
                return True  # if API fails, don't auto-fail
            data = await r.json()
            verdict = (data.get("verdict") or "").lower()
            if "honeypot" in verdict or "malicious" in verdict or "scam" in verdict:
                return False
    except Exception:
        return True
    return True

def looks_like_meme(name: str, symbol: str) -> bool:
    blob = f"{name} {symbol}"
    return MEME_RE.search(blob) is not None

def memecoin_score(pair: Dict[str, Any]) -> float:
    # signals
    tx5 = safe_get(pair, "txns", "m5", default={}) or {}
    h1 = safe_get(pair, "txns", "h1", default={}) or {}
    vol5 = float(safe_get(pair, "volume", "m5", default=0) or 0)
    vol1h = float(safe_get(pair, "volume", "h1", default=0) or 0)
    buys5 = int(tx5.get("buys", 0))
    sells5 = int(tx5.get("sells", 0))
    bsr5 = buys5 / max(1, sells5)

    liq = float(safe_get(pair, "liquidity", "usd", default=0) or 0)
    fdv = float(pair.get("fdv") or 0)
    price_change_5m = float(safe_get(pair, "priceChange", "m5", default=0) or 0)
    price_change_1h = float(safe_get(pair, "priceChange", "h1", default=0) or 0)

    # scoring heuristic
    score = 0.0
    score += max(0, (bsr5 - 1.0)) * 25            # buy pressure last 5m
    score += min(vol5 / 2000, 5) * 8              # raw velocity (cap at 5)
    score += min(vol1h / 10000, 5) * 6            # sustained flow
    score += max(0, price_change_5m) * 0.3        # momentum
    score += max(0, price_change_1h) * 0.15

    # liquidity sweat spot bonus
    if MIN_LIQ_USD <= liq <= MAX_LIQ_USD:
        score += 8

    # tiny penalty for very high FDV
    score -= max(0, (fdv - MAX_FDV) / 1_000_000)

    return round(score, 2)

def passes_hard_filters(pair: Dict[str, Any]) -> bool:
    created = pair.get("pairCreatedAt")  # ms
    if not created:
        return False
    age_min = (now_ms() - created) / 60000
    if age_min > MAX_TOKEN_AGE_MINUTES:
        return False

    liq = float(safe_get(pair, "liquidity", "usd", default=0) or 0)
    if liq < MIN_LIQ_USD or liq > MAX_LIQ_USD:
        return False

    fdv = float(pair.get("fdv") or 0)
    if fdv <= 0 or fdv > MAX_FDV:
        return False

    tx5 = safe_get(pair, "txns", "m5", default={}) or {}
    buys5 = int(tx5.get("buys", 0))
    sells5 = int(tx5.get("sells", 0))
    txns5 = buys5 + sells5
    bsr5 = buys5 / max(1, sells5)

    vol5 = float(safe_get(pair, "volume", "m5", default=0) or 0)

    if txns5 < MIN_TXNS_5M:
        return False
    if bsr5 < MIN_BUY_SELL_RATIO_5M:
        return False
    if vol5 < MIN_VOLUME_5M:
        return False

    # optional holders hint if present (rare)
    holders = int(pair.get("holders", 0) or 0)
    if MIN_HOLDERS_HINT and holders < MIN_HOLDERS_HINT:
        return False

    # keyword filter
    base = pair.get("baseToken") or {}
    if not looks_like_meme(base.get("name", ""), base.get("symbol", "")):
        return False

    return True

async def dex_query(params: Dict[str, str]) -> List[Dict[str, Any]]:
    """Generic DexScreener GET with params."""
    global _http
    try:
        async with _http.get(DEX_SEARCH_URL, params=params, timeout=15) as r:
            if r.status != 200:
                return []
            data = await r.json()
            return data.get("pairs") or []
    except Exception:
        return []

async def fetch_candidate_pairs() -> List[Dict[str, Any]]:
    """
    Strategy:
      1) keyword searches (e.g., 'pepe', 'doge', ...)
      2) generic search 'sol' fallback
      3) de-dupe by base mint address
    """
    # Collect results
    seen = set()
    pairs: List[Dict[str, Any]] = []

    # keyword queries first
    for kw in KEYWORDS[:10]:  # cap to avoid rate limits
        found = await dex_query({"q": f"{kw} solana"})
        for p in found:
            base_addr = safe_get(p, "baseToken", "address", default="")
            if not base_addr or base_addr in seen:
                continue
            if (p.get("chain") or "").lower() != "solana":
                continue
            seen.add(base_addr)
            pairs.append(p)

    # fallback wide search
    if len(pairs) < 30:
        more = await dex_query({"q": "solana"})
        for p in more:
            base_addr = safe_get(p, "baseToken", "address", default="")
            if not base_addr or base_addr in seen:
                continue
            if (p.get("chain") or "").lower() != "solana":
                continue
            seen.add(base_addr)
            pairs.append(p)

    return pairs

async def find_memecoin_candidates() -> List[Dict[str, Any]]:
    """Return sorted list of candidate dicts that pass filters + scoring + rugcheck."""
    raw_pairs = await fetch_candidate_pairs()
    filtered = [p for p in raw_pairs if passes_hard_filters(p)]

    # build enriched objects + apply score threshold + rugcheck
    out: List[Dict[str, Any]] = []
    for p in filtered:
        base = p.get("baseToken") or {}
        mint = base.get("address")
        if not mint:
            continue
        if not await rugcheck_ok(mint):
            continue
        score = memecoin_score(p)
        if score < SCORE_THRESHOLD:
            continue

        name = base.get("name", "Unknown")
        symbol = base.get("symbol", "")
        liq = float(safe_get(p, "liquidity", "usd", default=0) or 0)
        fdv = float(p.get("fdv") or 0)
        created = p.get("pairCreatedAt")
        age_str = ms_to_ago_str(created) if created else "n/a"
        price_usd = p.get("priceUsd")
        url = p.get("url")
        tx5 = safe_get(p, "txns", "m5", default={}) or {}
        vol5 = float(safe_get(p, "volume", "m5", default=0) or 0)
        buys5 = int(tx5.get("buys", 0)); sells5 = int(tx5.get("sells", 0))
        bsr5 = round(buys5 / max(1, sells5), 2)

        out.append({
            "name": name,
            "symbol": symbol,
            "mint": mint,
            "score": score,
            "age": age_str,
            "liq_usd": liq,
            "fdv": fdv,
            "price_usd": price_usd,
            "url": url,
            "bsr5": bsr5,
            "vol5": vol5,
            "links": link_bundle(mint),
        })

    out.sort(key=lambda x: x["score"], reverse=True)
    return out

# =========================
# DISCORD
# =========================
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user} | Guilds: {[g.name for g in bot.guilds]}")
    try:
        memescanner.start()
    except RuntimeError:
        # already running
        pass

@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")

@bot.command()
async def top(ctx, n: int = 3):
    """Manually fetch & post top N candidates."""
    await ctx.send("🔎 Scanning…")
    cands = await find_memecoin_candidates()
    if not cands:
        await ctx.send("No candidates right now.")
        return
    for c in cands[:max(1, n)]:
        await ctx.send(format_candidate_msg(c))

def format_candidate_msg(c: Dict[str, Any]) -> str:
    return (
        f"🚀 **{c['name']}** ({c['symbol']})\n"
        f"• Score: **{c['score']}** | Age: {c['age']}\n"
        f"• Price: ${c['price_usd']} | LQ: ${int(c['liq_usd']):,} | FDV: ${int(c['fdv']):,}\n"
        f"• 5m Buys/Sells: **{c['bsr5']}** | 5m Vol: ${int(c['vol5']):,}\n"
        f"🔗 DexScreener: {c['url'] or c['links']['dexscreener_search']}\n"
        f"🔗 Pump.fun: {c['links']['pumpfun']}\n"
        f"🔗 Axiom Surge: {c['links']['axiom']}\n"
        f"— *Heuristics only. Not financial advice.*"
    )

@tasks.loop(seconds=POST_EVERY_SECONDS)
async def memescanner():
    if CHANNEL_ID == 0:
        print("⚠️ CHANNEL_ID not set; skipping post.")
        return
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("⚠️ Bot cannot see channel. Check permissions / correct ID.")
        return
    try:
        cands = await find_memecoin_candidates()
        if not cands:
            print("No candidates found in this cycle.")
            return
        top = cands[0]
        await channel.send(format_candidate_msg(top))
        print(f"Posted: {top['name']} ({top['symbol']}) score={top['score']}")
    except Exception as e:
        print("Error in memescanner loop:", e)

# =========================
# API
# =========================
@app.get("/health")
async def health():
    return {"ok": True, "uptime": time.time()}

@app.get("/candidates")
async def candidates(limit: int = 5):
    cands = await find_memecoin_candidates()
    return JSONResponse({"count": len(cands[:limit]), "candidates": cands[:limit]})

# =========================
# BOOTSTRAP
# =========================
async def start_api():
    port = int(os.getenv("PORT", "8000"))
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

async def start_bot():
    await bot.start(DISCORD_TOKEN)

async def main():
    global _http
    if not DISCORD_TOKEN or CHANNEL_ID == 0:
        raise RuntimeError("Missing DISCORD_TOKEN and/or CHANNEL_ID in environment.")

    _http = aiohttp.ClientSession(headers={"User-Agent": "meme-scanner/1.0"})
    try:
        await asyncio.gather(start_api(), start_bot())
    finally:
        await _http.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down…")


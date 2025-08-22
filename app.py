import os, httpx, asyncio, threading
from typing import Dict, Any, List, Optional
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import discord
from discord.ext import tasks

# ---------------- CONFIG ----------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))

DEXSCREENER_NEW_TOKENS = "https://api.dexscreener.com/latest/dex/pairs/solana"
RUGCHECK_TOKEN = "https://api.rugcheck.xyz/v1/tokens/{}"

http = httpx.Client(timeout=15.0, headers={"User-Agent": "scanner/0.1"})
app = FastAPI(title="Pump.fun + Axiom scanner")

# ---------------- Helpers ----------------
def fetch_pairs() -> List[Dict[str, Any]]:
    try:
        r = http.get(DEXSCREENER_NEW_TOKENS)
        r.raise_for_status()
        return r.json().get("pairs") or []
    except Exception:
        return []

def rugcheck(mint: str) -> bool:
    try:
        r = http.get(RUGCHECK_TOKEN.format(mint))
        if r.status_code == 200:
            rc = r.json()
            verdict = (rc.get("verdict") or "").lower()
            if "honeypot" in verdict or "malicious" in verdict:
                return False
    except Exception:
        pass
    return True

def momentum_score(pair: Dict[str, Any]) -> float:
    tx = (pair.get("txns") or {}).get("h1") or {}
    buys, sells = tx.get("buys", 0), tx.get("sells", 0)
    vol = float((pair.get("volume") or {}).get("h1") or 0)
    liq = float((pair.get("liquidity") or {}).get("usd") or 0)
    bsr = (buys / max(1, sells)) if buys + sells > 0 else 0
    return (bsr * 20) + (vol / 10000) + (liq / 20000)

def links(mint: str) -> Dict[str, str]:
    return {
        "dexscreener": f"https://dexscreener.com/solana?q={mint}",
        "pumpfun": f"https://www.pump.fun/coin/{mint}",
        "axiom": f"https://axiom.trade/pulse?search={mint}",
        "axiom_home": "https://axiom.trade/"
    }

def build_candidate(p: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    base = p.get("baseToken") or {}
    mint = base.get("address")
    if not mint or not rugcheck(mint):
        return None
    score = momentum_score(p)
    if score < 40:
        return None
    return {
        "name": base.get("name"),
        "symbol": base.get("symbol"),
        "mint": mint,
        "score": round(score, 2),
        "links": links(mint),
        "liq_usd": (p.get("liquidity") or {}).get("usd")
    }

# ---------------- FastAPI routes ----------------
@app.get("/candidates")
def candidates(limit: int = 5):
    out = []
    for p in fetch_pairs():
        c = build_candidate(p)
        if c: out.append(c)
    out.sort(key=lambda x: x["score"], reverse=True)
    return JSONResponse({"count": len(out[:limit]), "candidates": out[:limit]})

@app.get("/health")
def health(): return {"ok": True}

# ---------------- Discord bot ----------------
intents = discord.Intents.default()
client = discord.Client(intents=intents)

@tasks.loop(seconds=60)
async def scan_and_post():
    if CHANNEL_ID == 0: return
    chan = client.get_channel(CHANNEL_ID)
    if not chan: return
    cands = [build_candidate(p) for p in fetch_pairs()]
    cands = [c for c in cands if c]
    if not cands: return
    top = max(cands, key=lambda x: x["score"])
    msg = (f"🚀 {top['name']} ({top['symbol']})\n"
           f"Score: {top['score']} | LQ: ${top['liq_usd']}\n"
           f"{top['links']['pumpfun']} | {top['links']['axiom']}")
    await chan.send(msg)

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    scan_and_post.start()

def run_discord():
    if DISCORD_TOKEN and CHANNEL_ID != 0:
        client.run(DISCORD_TOKEN)

# ---------------- Startup hack ----------------
def start_discord_in_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    run_discord()

threading.Thread(target=start_discord_in_thread, daemon=True).start()


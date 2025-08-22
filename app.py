import os
import httpx
import asyncio
import threading
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
app = FastAPI(title="Pump.fun + Axiom Scanner")

# ---------------- Helpers ----------------
def fetch_pairs():
    try:
        r = http.get(DEXSCREENER_NEW_TOKENS)
        r.raise_for_status()
        return r.json().get("pairs") or []
    except Exception as e:
        print("Error fetching pairs:", e)
        return []

def rugcheck(mint: str) -> bool:
    try:
        r = http.get(RUGCHECK_TOKEN.format(mint))
        if r.status_code == 200:
            rc = r.json()
            verdict = (rc.get("verdict") or "").lower()
            if "honeypot" in verdict or "malicious" in verdict:
                return False
    except Exception as e:
        print("Rugcheck error:", e)
    return True

def momentum_score(pair) -> float:
    tx = (pair.get("txns") or {}).get("h1") or {}
    buys, sells = tx.get("buys", 0), tx.get("sells", 0)
    vol = float((pair.get("volume") or {}).get("h1") or 0)
    liq = float((pair.get("liquidity") or {}).get("usd") or 0)
    bsr = (buys / max(1, sells)) if buys + sells > 0 else 0
    return (bsr * 20) + (vol / 10000) + (liq / 20000)

def links(mint: str):
    return {
        "dexscreener": f"https://dexscreener.com/solana?q={mint}",
        "pumpfun": f"https://www.pump.fun/coin/{mint}",
        "axiom": f"https://axiom.trade/pulse?search={mint}",
        "axiom_home": "https://axiom.trade/"
    }

def build_candidate(p):
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

# ---------------- FastAPI ----------------
@app.get("/candidates")
def candidates(limit: int = 5):
    out = []
    for p in fetch_pairs():
        c = build_candidate(p)
        if c: out.append(c)
    out.sort(key=lambda x: x["score"], reverse=True)
    return JSONResponse({"count": len(out[:limit]), "candidates": out[:limit]})

@app.get("/health")
def health(): 
    return {"ok": True}

# ---------------- Discord bot ----------------
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

@tasks.loop(seconds=60)
async def scan_and_post():
    if CHANNEL_ID == 0: 
        print("CHANNEL_ID not set")
        return
    chan = client.get_channel(CHANNEL_ID)
    if not chan: 
        print("Discord channel not found")
        return
    cands = [build_candidate(p) for p in fetch_pairs()]
    cands = [c for c in cands if c]
    if not cands: 
        print("No candidates found this round")
        return
    top = max(cands, key=lambda x: x["score"])
    msg = (f"🚀 {top['name']} ({top['symbol']})\n"
           f"Score: {top['score']} | LQ: ${top['liq_usd']}\n"
           f"{top['links']['pumpfun']} | {top['links']['axiom']}")
    try:
        await chan.send(msg)
        print(f"Posted to Discord: {top['name']}")
    except Exception as e:
        print("Error sending Discord message:", e)

@client.event
async def on_ready():
    print(f"Discord logged in as {client.user}")
    scan_and_post.start()

# ---------------- Startup ----------------
def start_api():
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)

# Start API in background thread
threading.Thread(target=start_api, daemon=True).start()

# Start Discord bot in main thread
if DISCORD_TOKEN and CHANNEL_ID != 0:
    client.run(DISCORD_TOKEN)


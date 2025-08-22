import os
import httpx
import asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import discord
from discord.ext import tasks, commands
import uvicorn

# ---------------- CONFIG ----------------
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID", 0))  # numeric channel ID

DEXSCREENER_API = "https://api.dexscreener.com/latest/dex/pairs"
RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens/{}"

http = httpx.Client(timeout=15.0, headers={"User-Agent": "pump-scanner/1.0"})
app = FastAPI(title="Pump.fun + Axiom Scanner")

# ---------------- HELPERS ----------------
def fetch_pairs():
    try:
        r = http.get(DEXSCREENER_API)
        r.raise_for_status()
        all_pairs = r.json().get("pairs") or []
        sol_pairs = [p for p in all_pairs if (p.get("chain") or "").lower() == "solana"]
        return sol_pairs
    except Exception as e:
        print("Error fetching pairs:", e)
        return []

def rugcheck(mint: str) -> bool:
    try:
        r = http.get(RUGCHECK_API.format(mint))
        if r.status_code == 200:
            verdict = (r.json().get("verdict") or "").lower()
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

def build_candidate(pair):
    base = pair.get("baseToken") or {}
    mint = base.get("address")
    if not mint or not rugcheck(mint):
        return None
    score = momentum_score(pair)
    if score < 40:  # arbitrary threshold
        return None
    return {
        "name": base.get("name"),
        "symbol": base.get("symbol"),
        "mint": mint,
        "score": round(score, 2),
        "links": links(mint),
        "liq_usd": (pair.get("liquidity") or {}).get("usd")
    }

# ---------------- FASTAPI ----------------
@app.get("/health")
def health():
    return {"ok": True}

@app.get("/candidates")
def candidates(limit: int = 5):
    out = [build_candidate(p) for p in fetch_pairs()]
    out = [c for c in out if c]
    out.sort(key=lambda x: x["score"], reverse=True)
    return JSONResponse({"count": len(out[:limit]), "candidates": out[:limit]})

# ---------------- DISCORD BOT ----------------
intents = discord.Intents.default()
intents.message_content = False  # Only enable if you read messages
bot = commands.Bot(command_prefix="!", intents=intents)

@tasks.loop(seconds=60)
async def scan_and_post():
    if CHANNEL_ID == 0:
        print("CHANNEL_ID not set")
        return
    channel = bot.get_channel(CHANNEL_ID)
    if not channel:
        print("Discord channel not found")
        return
    cands = [build_candidate(p) for p in fetch_pairs()]
    cands = [c for c in cands if c]
    if not cands:
        print("No candidates found")
        return
    top = max(cands, key=lambda x: x["score"])
    msg = (
        f"🚀 {top['name']} ({top['symbol']})\n"
        f"Score: {top['score']} | LQ: ${top['liq_usd']}\n"
        f"{top['links']['pumpfun']} | {top['links']['axiom']}"
    )
    try:
        await channel.send(msg)
        print(f"Posted to Discord: {top['name']}")
    except Exception as e:
        print("Error sending message:", e)

@bot.event
async def on_ready():
    print(f"Discord logged in as {bot.user}")
    scan_and_post.start()

# ---------------- RUN BOTH ----------------
async def main():
    port = int(os.getenv("PORT", 8000))
    config = uvicorn.Config("app:app", host="0.0.0.0", port=port, log_level="info")
    server = uvicorn.Server(config)

    # Run both FastAPI and Discord bot concurrently
    await asyncio.gather(
        server.serve(),
        bot.start(DISCORD_TOKEN)
    )

if __name__ == "__main__":
    asyncio.run(main())


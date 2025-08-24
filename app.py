import os
import aiohttp
import discord
from discord.ext import tasks
from dotenv import load_dotenv

# -----------------------
# Load environment variables
# -----------------------
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))

# -----------------------
# Discord Client Setup
# -----------------------
intents = discord.Intents.default()
client = discord.Client(intents=intents)

# -----------------------
# API Endpoints
# -----------------------
AXIOM_URL = "https://api.axiom.xyz/trending"
PUMPFUN_URL = "https://pump.fun/api/trending"

# -----------------------
# Fetch JSON Helper
# -----------------------
async def fetch_json(session, url, headers=None):
    try:
        async with session.get(url, headers=headers) as resp:
            return await resp.json()
    except Exception as e:
        print(f"[ERROR] Fetching {url}: {e}")
        return None

# -----------------------
# Scan Memecoins (Filters Off)
# -----------------------
async def scan_memecoins():
    results = []
    async with aiohttp.ClientSession() as session:
        print("[DEBUG] Starting memecoin scan...")

        # Axiom Surge
        axiom_data = await fetch_json(session, AXIOM_URL)
        if axiom_data and "trending" in axiom_data:
            print(f"[DEBUG] Found {len(axiom_data['trending'])} coins on 
Axiom")
            for coin in axiom_data["trending"]:
                results.append({
                    "name": coin["name"],
                    "symbol": coin["symbol"],
                    "link": f"https://axiom.xyz/token/{coin['id']}",
                    "marketCap": coin.get("marketCap", "N/A")
                })

        # Pump.fun
        pump_data = await fetch_json(session, PUMPFUN_URL)
        if pump_data and "coins" in pump_data:
            print(f"[DEBUG] Found {len(pump_data['coins'])} coins on 
Pump.fun")
            for coin in pump_data["coins"]:
                results.append({
                    "name": coin["name"],
                    "symbol": coin["symbol"],
                    "link": f"https://pump.fun/{coin['mint']}",
                    "marketCap": coin.get("marketCap", "N/A")
                })

    print(f"[DEBUG] Total coins collected this cycle: {len(results)}")
    return results

# -----------------------
# Discord Posting Loop
# -----------------------
@tasks.loop(minutes=5)
async def post_trending():
    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        print("[WARN] Bot could not find the channel.")
        return

    coins = await scan_memecoins()
    if not coins:
        print("[DEBUG] No coins found this cycle.")
        return

    for coin in coins:
        msg = f"🔥 **{coin['name']} ({coin['symbol']})**\n💰 MC: 
{coin['marketCap']}\n🔗 {coin['link']}"
        await channel.send(msg)

# -----------------------
# On Ready Event
# -----------------------
@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("✅ Bot is live and ready! Test message sent.")
    await post_trending()  # Run immediately once
    post_trending.start()   # Start loop every 5 minutes

# -----------------------
# Run Bot
# -----------------------
client.run(DISCORD_TOKEN)


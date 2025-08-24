import os
import aiohttp
import asyncio
import discord
from discord.ext import tasks
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
TWITTER_BEARER = os.getenv("TWITTER_BEARER")  # optional

intents = discord.Intents.default()
client = discord.Client(intents=intents)

# -----------------------
# API Endpoints
# -----------------------
AXIOM_URL = "https://api.axiom.xyz/trending"
PUMPFUN_URL = "https://pump.fun/api/trending"
TWITTER_URL = "https://api.twitter.com/2/tweets/search/recent"

# -----------------------
# Scraper
# -----------------------
async def fetch_json(session, url, headers=None):
    try:
        async with session.get(url, headers=headers) as resp:
            return await resp.json()
    except Exception as e:
        print(f"[ERROR] fetching {url}: {e}")
        return None

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
                # Filters temporarily disabled
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
                # Filters temporarily disabled
                results.append({
                    "name": coin["name"],
                    "symbol": coin["symbol"],
                    "link": f"https://pump.fun/{coin['mint']}",
                    "marketCap": coin.get("marketCap", "N/A")
                })

        # Twitter memecoin hashtag (optional)
        if TWITTER_BEARER:
            headers = {"Authorization": f"Bearer {TWITTER_BEARER}"}
            twitter_data = await fetch_json(session, TWITTER_URL + 
"?query=%23memecoin&max_results=5", headers=headers)
            if twitter_data and "data" in twitter_data:
                print(f"[DEBUG] Found {len(twitter_data['data'])} tweets 
on #memecoin")
                for tweet in twitter_data["data"]:
                    results.append({
                        "name": "Tweet",
                        "symbol": "X",
                        "link": 
f"https://twitter.com/i/web/status/{tweet['id']}",
                        "marketCap": "N/A"
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
# On Ready
# -----------------------
@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("✅ Bot is live and ready! Test message sent.")
    await post_trending()  # run once immediately
    post_trending.start()   # continue every 5 minutes

# -----------------------
# Run Bot
# -----------------------
client.run(DISCORD_TOKEN)


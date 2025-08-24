import os
import aiohttp
import asyncio
import discord
from discord.ext import tasks
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("CHANNEL_ID"))
TWITTER_BEARER = os.getenv("TWITTER_BEARER")  # optional

intents = discord.Intents.default()
client = discord.Client(intents=intents)

# -----------------------
# API Endpoints
# -----------------------
AXIOM_URL = "https://api.axiom.xyz/trending"  # primary focus
PUMPFUN_URL = "https://pump.fun/api/trending"  # optional backup
TWITTER_URL = "https://api.twitter.com/2/tweets/search/recent"

# -----------------------
# Relaxed Filter Logic
# -----------------------
def is_good_coin(data):
    """Relaxed: only filter out coins with zero liquidity or invalid 
marketCap"""
    mc = data.get("marketCap", 0)
    liquidity = data.get("liquidity", {}).get("usd", 0)
    if mc <= 0 or liquidity <= 0:
        return False
    return True

# -----------------------
# Scraper
# -----------------------
async def fetch_json(session, url, headers=None):
    try:
        async with session.get(url, headers=headers) as resp:
            data = await resp.json()
            return data
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

async def scan_memecoins():
    results = []
    async with aiohttp.ClientSession() as session:
        # -----------------------
        # Axiom Surge (primary focus)
        # -----------------------
        axiom_data = await fetch_json(session, AXIOM_URL)
        if axiom_data and "trending" in axiom_data:
            print(f"[DEBUG] Found {len(axiom_data['trending'])} coins on 
Axiom")
            for coin in axiom_data["trending"]:
                if is_good_coin(coin):
                    results.append({
                        "name": coin["name"],
                        "symbol": coin["symbol"],
                        "link": f"https://axiom.xyz/token/{coin['id']}",
                        "marketCap": coin.get("marketCap", "N/A")
                    })

        # -----------------------
        # Pump.fun (optional)
        # -----------------------
        pump_data = await fetch_json(session, PUMPFUN_URL)
        if pump_data and "coins" in pump_data:
            print(f"[DEBUG] Found {len(pump_data['coins'])} coins on 
Pump.fun")
            for coin in pump_data["coins"]:
                if is_good_coin(coin):
                    results.append({
                        "name": coin["name"],
                        "symbol": coin["symbol"],
                        "link": f"https://pump.fun/{coin['mint']}",
                        "marketCap": coin.get("marketCap", "N/A")
                    })

        # -----------------------
        # Twitter memecoin hashtag (optional)
        # -----------------------
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

    return results

# -----------------------
# Discord Posting Loop
# -----------------------
@tasks.loop(minutes=5)
async def post_trending():
    channel = client.get_channel(CHANNEL_ID)
    if not channel:
        print("⚠️ Bot could not find the channel.")
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
    # Test message
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("✅ Bot is live and ready! Test message sent.")
    post_trending.start()

# -----------------------
# Run Bot
# -----------------------
client.run(DISCORD_TOKEN)


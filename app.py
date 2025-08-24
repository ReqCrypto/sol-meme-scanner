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
DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/solana"
PUMPFUN_URL = "https://pump.fun/api/trending"
AXIOM_URL = "https://api.axiom.xyz/trending"
TWITTER_URL = "https://api.twitter.com/2/tweets/search/recent"

# -----------------------
# Filter Logic
# -----------------------
def is_good_coin(data):
    mc = data.get("marketCap", 0)
    liquidity = data.get("liquidity", {}).get("usd", 0)
    buys = data.get("buys", 0)
    sells = data.get("sells", 0)

    if mc < 50000 or mc > 5000000:
        return False
    if liquidity < 10000:
        return False
    if sells > buys:
        return False
    return True

# -----------------------
# Scraper
# -----------------------
async def fetch_json(session, url, headers=None):
    try:
        async with session.get(url, headers=headers) as resp:
            return await resp.json()
    except Exception as e:
        print(f"Error fetching {url}: {e}")
        return None

async def scan_memecoins():
    results = []
    async with aiohttp.ClientSession() as session:
        # Pump.fun
        pump_data = await fetch_json(session, PUMPFUN_URL)
        if pump_data:
            for coin in pump_data.get("coins", []):
                if is_good_coin(coin):
                    results.append({
                        "name": coin["name"],
                        "symbol": coin["symbol"],
                        "link": f"https://pump.fun/{coin['mint']}",
                        "marketCap": coin["marketCap"]
                    })

        # Axiom
        axiom_data = await fetch_json(session, AXIOM_URL)
        if axiom_data:
            for coin in axiom_data.get("trending", []):
                if is_good_coin(coin):
                    results.append({
                        "name": coin["name"],
                        "symbol": coin["symbol"],
                        "link": f"https://axiom.xyz/token/{coin['id']}",
                        "marketCap": coin["marketCap"]
                    })

        # Twitter Memecoin hashtag
        if TWITTER_BEARER:
            headers = {"Authorization": f"Bearer {TWITTER_BEARER}"}
            twitter_data = await fetch_json(session, TWITTER_URL + "?query=%23memecoin&max_results=5", headers=headers)
            if twitter_data:
                for tweet in twitter_data.get("data", []):
                    results.append({
                        "name": "Tweet",
                        "symbol": "X",
                        "link": f"https://twitter.com/i/web/status/{tweet['id']}",
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
        print("No coins found this cycle.")
        return

    for coin in coins:
        msg = f"🔥 **{coin['name']} ({coin['symbol']})**\n💰 MC: {coin['marketCap']}\n🔗 {coin['link']}"
        await channel.send(msg)

# -----------------------
# On Ready
# -----------------------
@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    # Post test message immediately
    channel = client.get_channel(CHANNEL_ID)
    if channel:
        await channel.send("✅ Bot is live and ready! Test message sent.")
    post_trending.start()

# -----------------------
# Run Bot
# -----------------------
client.run(DISCORD_TOKEN)


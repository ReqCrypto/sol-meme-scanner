import os
import httpx
import asyncio
from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI()

@app.get("/")
async def root():
    return {"status": "ok", "message": "Pump scanner is running"}

@app.get("/health")
async def health():
    return {"status": "healthy"}

@app.get("/candidates")
async def candidates(limit: int = 5, min_score: int = 40):
    # 🔹 Placeholder scanner logic
    # You can replace this with pump.fun + axiom API calls
    sample = [
        {
            "name": "MOONCOIN",
            "mint": "ABCDEFG123456",
            "pumpfun_link": f"https://pump.fun/coin/ABCDEFG123456",
            "axiom_link": f"https://axiom.xyz/token/ABCDEFG123456",
            "dexscreener": f"https://dexscreener.com/solana/ABCDEFG123456",
            "projected_market_cap": "2.5M",
            "score": 85,
        }
    ]
    return JSONResponse(content={"limit": limit, "results": sample[:limit]})

# 🔹 Entry point for Render & local
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))  # Render injects PORT, default=8000 local
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)


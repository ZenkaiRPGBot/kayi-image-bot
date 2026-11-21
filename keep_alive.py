import os
import asyncio
from datetime import datetime
import aiohttp
from aiohttp import web
from config import PORT, PING_URL, BRAND_NAME, IST

async def handle_ping(request):
    now_str = datetime.now(IST).strftime("%I:%M:%S %p IST · %b %d")
    return web.Response(
        text=f"{BRAND_NAME} is active & healthy.\nTime: {now_str}\nStatus: 200 OK",
        content_type="text/plain"
    )

async def self_ping_loop():
    """Keeps the service awake on Render Free Tier."""
    await asyncio.sleep(15)
    if not PING_URL:
        return

    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(PING_URL, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    pass
            except Exception:
                pass
            await asyncio.sleep(600)  # Ping every 10 minutes

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    app.router.add_get("/ping", handle_ping)
    app.router.add_get("/health", handle_ping)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"[web] Keep-alive server running on port {PORT}")

    if PING_URL:
        asyncio.create_task(self_ping_loop())

    return runner

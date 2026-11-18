from __future__ import annotations

import time

import aiohttp

from .db import Database
from .windows import humanize_remaining


def health_payload(db: Database, now: int | None = None) -> dict:
    last = db.last_poll("auctions")
    nonempty = db.last_nonempty_poll("auctions")
    prices = db.last_poll("prices")
    now = int(time.time()) if now is None else now
    healthy = last is None or (last.ok and last.items != 0)
    return {
        "status": "ok" if healthy else "degraded",
        "lots_active": db.counts().lots_active,
        "last_auction_poll_age_s": None if last is None else now - last.started_at,
        "last_nonempty_poll_age_s": None if nonempty is None else now - nonempty.started_at,
        "last_price_poll_age_s": None if prices is None else now - prices.started_at,
    }


def create_app(settings, db: Database):
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("install the web extra: pip install -e '.[web]'") from exc

    app = FastAPI(title="topbot", docs_url=None, redoc_url=None)

    @app.get("/healthz")
    async def healthz():
        return JSONResponse(health_payload(db))

    @app.get("/api/lots/ending")
    async def ending(within: int = 86400):
        rows = db.conn.execute(
            "SELECT id, title, end_ts, current_bid, start_bid, seller_name, seller_city FROM lots"
            " WHERE state='active' AND end_ts BETWEEN ? AND ? ORDER BY end_ts LIMIT 100",
            (int(time.time()), int(time.time()) + within),
        ).fetchall()
        return JSONResponse({"lots": [
            {
                "id": row["id"],
                "title": row["title"],
                "remaining": humanize_remaining(int(row["end_ts"]) - int(time.time())),
                "price": max(int(row["current_bid"] or 0), int(row["start_bid"] or 0)),
                "seller": row["seller_name"],
                "city": row["seller_city"],
                "url": f"https://topdeck.ru/apps/toptrade/auctions/{row['id']}",
            }
            for row in rows
        ]})

    @app.get("/", response_class=HTMLResponse)
    async def index():
        rows = db.conn.execute(
            "SELECT id, title, end_ts, current_bid, start_bid, seller_name FROM lots"
            " WHERE state='active' ORDER BY end_ts LIMIT 30"
        ).fetchall()
        items = "".join(
            f"<li><a href='https://topdeck.ru/apps/toptrade/auctions/{row['id']}' target='_blank' rel='noopener'>"
            f"{row['title']}</a> — {humanize_remaining(int(row['end_ts']) - int(time.time()))}</li>"
            for row in rows
        )
        return HTMLResponse(
            "<!doctype html><meta charset='utf-8'><title>topbot</title>"
            "<h1>Скоро закончится</h1><ul>" + (items or "<li>пока пусто</li>") + "</ul>"
        )

    return app


async def serve(settings, db: Database) -> None:
    import uvicorn

    config = uvicorn.Config(create_app(settings, db), host=settings.web_bind, port=settings.web_port,
                            log_level="warning")
    await uvicorn.Server(config).serve()


def make_session() -> aiohttp.ClientSession:
    import socket

    connector = aiohttp.TCPConnector(family=socket.AF_INET, ttl_dns_cache=300, limit=20)
    timeout = aiohttp.ClientTimeout(total=90, connect=15, sock_read=60)
    return aiohttp.ClientSession(connector=connector, timeout=timeout)

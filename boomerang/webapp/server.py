"""Dashboard server (Starlette + uvicorn). Runs in parallel with the agent.

Endpoints (all require ?key=TOKEN):
  GET /dash          → dashboard HTML page
  GET /api/status    → current state (reads state/agent_state.json)
  GET /api/trades    → trade history (reads state/trades.json)
"""
from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Callable

import uvicorn
from starlette.applications import Starlette
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse
from starlette.routing import Route

from boomerang.persistence import load_state, load_trades

_HTML = (Path(__file__).parent / "dashboard.html").read_text(encoding="utf-8")
_log = logging.getLogger("boomerang.webapp")

# Cache of the on-chain breakdown: 1 RPC read shared by all viewers.
_WALLET_CACHE_TTL = 25.0


def make_app(token: str, wallet_provider: Callable[[], dict] | None = None) -> Starlette:
    """wallet_provider: SYNCHRONOUS function that returns the wallet's on-chain breakdown
    (e.g.: validator.wallet_breakdown(addr)). If None, /api/wallet falls back to whatever
    is in the saved state (useful for tests without RPC)."""
    def ok(request) -> bool:  # noqa: ANN001
        return bool(token) and request.query_params.get("key") == token

    cache: dict = {"ts": 0.0, "data": None}

    async def dash(request):  # noqa: ANN001
        if not ok(request):
            return PlainTextResponse("403 — invalid token. Use /dashboard on Telegram.", status_code=403)
        return HTMLResponse(_HTML)

    async def api_status(request):  # noqa: ANN001
        if not ok(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse(load_state() or {})

    async def api_trades(request):  # noqa: ANN001
        if not ok(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse(load_trades())

    async def api_wallet(request):  # noqa: ANN001
        if not ok(request):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if wallet_provider is None:
            st = load_state() or {}
            return JSONResponse(st.get("wallet") or {"holdings": [], "total_usd": st.get("equity_usd")})
        now = time.monotonic()
        if cache["data"] is not None and now - cache["ts"] < _WALLET_CACHE_TTL:
            return JSONResponse(cache["data"])
        try:
            data = await asyncio.to_thread(wallet_provider)  # web3 is blocking → thread
            cache["data"], cache["ts"] = data, now
            return JSONResponse(data)
        except Exception as exc:  # noqa: BLE001
            _log.warning("api/wallet failed: %s", exc)
            stale = cache["data"] or {"holdings": [], "total_usd": None}
            return JSONResponse({**stale, "error": str(exc)})

    return Starlette(routes=[
        Route("/dash", dash),
        Route("/api/status", api_status),
        Route("/api/trades", api_trades),
        Route("/api/wallet", api_wallet),
    ])


async def serve(token: str, host: str = "0.0.0.0", port: int = 8080,
                wallet_provider: Callable[[], dict] | None = None) -> None:
    config = uvicorn.Config(make_app(token, wallet_provider), host=host, port=port, log_level="warning")
    await uvicorn.Server(config).serve()

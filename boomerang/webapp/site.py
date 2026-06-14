"""Boomerang AI public site (production) — landing, docs, guide, live proof and Console.

Server-side Starlette app (Jinja2 + hand-written CSS + Alpine/JS), no build. Serves for:
  - local dev:   python scripts/preview_web.py            (port 8090)
  - production:  uvicorn boomerang.webapp.site:app        (behind TLS/reverse proxy)

Everything here is public and read-only, EXCEPT the Console, which requires wallet login
(SIWE). On the public deploy the Console runs in DEMO mode (simulated agent per wallet),
so no real money is touched by the web. Real control of the agent is only via
Telegram (owner) / via the owner wallet.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from boomerang.identity import bnb_agent as identity
from boomerang.persistence import load_state, load_trades
from boomerang.webapp import auth, demo
from boomerang.webapp.i18n import docs_nav, nav_items, pick_lang, strings

SESSION_COOKIE = "bmrg_session"
WEB = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB / "templates"))
SOON: dict = {}


def _owner() -> str:
    return os.getenv("OWNER_WALLET_ADDRESS", "")


def _short(a: str) -> str:
    return (a[:6] + "…" + a[-4:]) if a else ""


def _lang(request) -> str:  # noqa: ANN001
    return pick_lang(request.query_params.get("lang"), request.cookies.get("lang"))


def _set_lang_cookie(request, resp):  # noqa: ANN001
    q = request.query_params.get("lang")
    if q in ("en", "pt"):
        resp.set_cookie("lang", q, max_age=31536000)
    return resp


def _resp(request, template, active, extra=None):  # noqa: ANN001
    lang = _lang(request)
    ctx = {"request": request, "lang": lang, "t": strings(lang),
           "nav": nav_items(lang), "active": active}
    if extra:
        ctx.update({k: v[lang] for k, v in extra.items()})
        ctx["back"] = "Voltar ao início" if lang == "pt" else "Back home"
    return _set_lang_cookie(request, templates.TemplateResponse(request, template, ctx))


async def home(request):  # noqa: ANN001
    return _resp(request, "landing.html", "/")


async def style(request):  # noqa: ANN001
    return _resp(request, "foundation.html", "/style")


async def docs_page(request):  # noqa: ANN001
    lang = _lang(request)
    ctx = {"request": request, "lang": lang, "t": strings(lang),
           "nav": nav_items(lang), "active": "/docs", "docs": docs_nav(lang),
           "docs_title": "Documentação" if lang == "pt" else "Documentation",
           "docs_label": "Nesta página" if lang == "pt" else "On this page"}
    return _set_lang_cookie(request, templates.TemplateResponse(request, "docs.html", ctx))


async def guides_page(request):  # noqa: ANN001
    lang = _lang(request)
    ctx = {"request": request, "lang": lang, "t": strings(lang), "nav": nav_items(lang),
           "active": "/guides", "guides_title": "Guia" if lang == "pt" else "Guide"}
    return _set_lang_cookie(request, templates.TemplateResponse(request, "guides.html", ctx))


async def live_page(request):  # noqa: ANN001
    return _resp(request, "live.html", "/live")


async def api_live(request):  # noqa: ANN001 — public, read-only of the persisted state
    return JSONResponse({"state": load_state() or {}, "trades": load_trades(),
                         "identity": identity.summary()})


# ── Console (owner) + SIWE ────────────────────────────────────────────────────
async def console_page(request):  # noqa: ANN001
    lang = _lang(request)
    addr = auth.check_session(request.cookies.get(SESSION_COOKIE))
    ctx = {"request": request, "lang": lang, "t": strings(lang), "nav": nav_items(lang),
           "active": "/console", "authed": bool(addr), "owner_short": _short(addr or ""), "demo": True}
    resp = _set_lang_cookie(request, templates.TemplateResponse(request, "console.html", ctx))
    # Never cache: the page depends on the session cookie. Without this, after login
    # the reload may serve the cached login version and "not log in".
    resp.headers["Cache-Control"] = "no-store, must-revalidate"
    return resp


async def auth_nonce(request):  # noqa: ANN001
    body = await request.json()
    addr = (body.get("address") or "").strip()
    if not addr:
        return JSONResponse({"error": "no address"}, status_code=400)
    domain = request.headers.get("host", "boomerang-ai")
    return JSONResponse({"message": auth.build_message(addr, auth.new_nonce(), domain)})


async def auth_verify(request):  # noqa: ANN001 — DEMO: any wallet (each one = its own simulated agent)
    body = await request.json()
    if not auth.verify_signer(body.get("address", ""), body.get("message", ""), body.get("signature", "")):
        return JSONResponse({"ok": False}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, auth.make_session(body["address"]),
                    httponly=True, samesite="lax", max_age=43200, path="/")
    return resp


async def auth_guest(request):  # noqa: ANN001 — DEMO: enters without a wallet (each guest = its own simulated agent)
    addr = "0x" + secrets.token_hex(20)  # random guest wallet, only for the demo session
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, auth.make_session(addr),
                    httponly=True, samesite="lax", max_age=43200, path="/")
    return resp


async def auth_logout(request):  # noqa: ANN001
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


async def console_state(request):  # noqa: ANN001
    addr = auth.check_session(request.cookies.get(SESSION_COOKIE))
    if not addr:
        return JSONResponse({"state": {}, "trades": []}, status_code=401)
    return JSONResponse(demo.snapshot(addr))


async def console_action(request):  # noqa: ANN001 — actions on the SIMULATED agent of the session wallet
    addr = auth.check_session(request.cookies.get(SESSION_COOKIE))
    if not addr:
        return JSONResponse({"ok": False, "detail": "not authenticated"}, status_code=401)
    name = request.path_params["name"]
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        pass
    if name == "start":
        ok, msg = demo.start(addr)
    elif name == "tick":  # advances 1 cycle of the simulated autonomous agent
        return JSONResponse({"ok": True, **demo.tick(addr)})
    elif name == "configure":
        ok, msg = demo.configure(addr, body.get("token_focus", "ALL"),
                                 body.get("stop_loss_pct", 4), body.get("take_profit_pct", 10))
    elif name == "pause":
        ok, msg = demo.pause(addr)
    elif name == "withdraw":
        ok, msg = demo.withdraw(addr)
    elif name == "panic":
        ok, msg = demo.panic(addr)
    else:
        ok, msg = False, "unknown action"
    return JSONResponse({"ok": ok, "detail": msg})


async def healthz(request):  # noqa: ANN001 — health check for the reverse proxy/uptime
    # Reflects the AGENT'S HEALTH: if the heartbeat got stale (> 5min), the agent
    # froze/died → 503 → Railway restarts the container. Covers deadlock (no exception).
    from boomerang.liveness import age_seconds
    age = age_seconds()
    if age > 300:
        return JSONResponse({"ok": False, "agent_stale_s": round(age)}, status_code=503)
    return JSONResponse({"ok": True, "agent_beat_s": round(age),
                         "identity": identity.summary().get("registered", False)})


# ── embedded x402 proxy ──────────────────────────────────────────────────────
# Gives `twak x402` (which runs locally, where the trade wallet is) a PUBLIC
# endpoint (the Railway URL) that injects the Accept header of CMC's MCP. This way
# the real payment settles without moving the wallet or spinning up a VPS.
_X402_TARGET = os.getenv("X402_TARGET", "https://mcp.coinmarketcap.com/x402/mcp")
_X402_FWD = ("payment-signature", "x-payment", "x-payment-signature", "mcp-protocol-version")


async def x402_proxy(request):  # noqa: ANN001
    body = await request.body()
    fwd = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    for k, v in request.headers.items():
        if k.lower() in _X402_FWD:
            fwd[k] = v
    try:
        async with httpx.AsyncClient(timeout=40) as cx:
            r = await cx.request(request.method, _X402_TARGET, content=body, headers=fwd)
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": f"proxy upstream: {exc}"}, status_code=502)
    out = {k: v for k, v in r.headers.items() if k.lower() in ("payment-required", "content-type")}
    return Response(r.content, status_code=r.status_code, headers=out)


def make_soon(path):  # noqa: ANN001
    async def handler(request):  # noqa: ANN001
        return _resp(request, "placeholder.html", path, SOON[path])
    return handler


def create_app() -> Starlette:
    routes = [
        Route("/", home), Route("/style", style), Route("/docs", docs_page),
        Route("/guides", guides_page), Route("/live", live_page),
        Route("/api/live", api_live), Route("/console", console_page),
        Route("/api/auth/nonce", auth_nonce, methods=["POST"]),
        Route("/api/auth/verify", auth_verify, methods=["POST"]),
        Route("/api/auth/guest", auth_guest, methods=["POST"]),
        Route("/api/auth/logout", auth_logout, methods=["POST"]),
        Route("/api/console/state", console_state),
        Route("/api/console/{name}", console_action, methods=["POST"]),
        Route("/healthz", healthz),
        Route("/x402", x402_proxy, methods=["GET", "POST"]),
        Route("/x402/{path:path}", x402_proxy, methods=["GET", "POST"]),
    ]
    for p in SOON:
        routes.append(Route(p, make_soon(p)))
    routes.append(Mount("/static", StaticFiles(directory=str(WEB / "static")), name="static"))
    return Starlette(routes=routes)


app = create_app()

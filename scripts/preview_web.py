"""Preview LOCAL da web do Boomerang AI (Fase 0) — porta 8090.

Serve a fundação (design system + shell) SEM tocar no agente que roda na 8080.
Rotas não-construídas mostram um placeholder com o mesmo shell.

Uso: .venv\\Scripts\\python scripts\\preview_web.py   → http://localhost:8090
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from boomerang.persistence import load_state, load_trades
from boomerang.webapp import auth, demo
from boomerang.webapp.i18n import docs_nav, nav_items, pick_lang, strings

load_dotenv()
OWNER = os.getenv("OWNER_WALLET_ADDRESS", "")
SESSION_COOKIE = "bmrg_session"


def _short(a: str) -> str:
    return (a[:6] + "…" + a[-4:]) if a else ""

WEB = Path(__file__).resolve().parent.parent / "boomerang" / "webapp"
templates = Jinja2Templates(directory=str(WEB / "templates"))

# Placeholders bilíngues para rotas ainda não construídas (nenhuma agora).
SOON: dict = {}


def _lang(request) -> str:  # noqa: ANN001
    return pick_lang(request.query_params.get("lang"), request.cookies.get("lang"))


def _resp(request, template, active, extra=None):  # noqa: ANN001
    lang = _lang(request)
    ctx = {"request": request, "lang": lang, "t": strings(lang),
           "nav": nav_items(lang), "active": active}
    if extra:
        ctx.update({k: v[lang] for k, v in extra.items()})
        ctx["back"] = "Voltar ao início" if lang == "pt" else "Back home"
    resp = templates.TemplateResponse(request, template, ctx)
    q = request.query_params.get("lang")
    if q in ("en", "pt"):
        resp.set_cookie("lang", q, max_age=31536000)
    return resp


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
    resp = templates.TemplateResponse(request, "docs.html", ctx)
    q = request.query_params.get("lang")
    if q in ("en", "pt"):
        resp.set_cookie("lang", q, max_age=31536000)
    return resp


async def guides_page(request):  # noqa: ANN001
    lang = _lang(request)
    ctx = {"request": request, "lang": lang, "t": strings(lang), "nav": nav_items(lang),
           "active": "/guides", "guides_title": "Guia" if lang == "pt" else "Guide"}
    resp = templates.TemplateResponse(request, "guides.html", ctx)
    q = request.query_params.get("lang")
    if q in ("en", "pt"):
        resp.set_cookie("lang", q, max_age=31536000)
    return resp


async def live_page(request):  # noqa: ANN001
    return _resp(request, "live.html", "/live")


async def api_live(request):  # noqa: ANN001 — público, só leitura do estado persistido
    return JSONResponse({"state": load_state() or {}, "trades": load_trades()})


# ── Console privado (dono) + SIWE ────────────────────────────────────────────
async def console_page(request):  # noqa: ANN001
    lang = _lang(request)
    addr = auth.check_session(request.cookies.get(SESSION_COOKIE))
    ctx = {"request": request, "lang": lang, "t": strings(lang), "nav": nav_items(lang),
           "active": "/console", "authed": bool(addr), "owner_short": _short(addr or ""), "demo": True}
    resp = templates.TemplateResponse(request, "console.html", ctx)
    q = request.query_params.get("lang")
    if q in ("en", "pt"):
        resp.set_cookie("lang", q, max_age=31536000)
    return resp


async def auth_nonce(request):  # noqa: ANN001
    body = await request.json()
    addr = (body.get("address") or "").strip()
    if not addr:
        return JSONResponse({"error": "no address"}, status_code=400)
    domain = request.headers.get("host", "boomerang-ai")
    return JSONResponse({"message": auth.build_message(addr, auth.new_nonce(), domain)})


async def auth_verify(request):  # noqa: ANN001 — DEMO: qualquer carteira (cada uma = agente simulado próprio)
    body = await request.json()
    if not auth.verify_signer(body.get("address", ""), body.get("message", ""), body.get("signature", "")):
        return JSONResponse({"ok": False}, status_code=401)
    resp = JSONResponse({"ok": True})
    resp.set_cookie(SESSION_COOKIE, auth.make_session(body["address"]),
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


async def console_action(request):  # noqa: ANN001 — ações no agente SIMULADO da carteira da sessão
    addr = auth.check_session(request.cookies.get(SESSION_COOKIE))
    if not addr:
        return JSONResponse({"ok": False, "detail": "não autenticado"}, status_code=401)
    name = request.path_params["name"]
    body = {}
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        pass
    if name == "configure":
        ok, msg = demo.configure(addr, body.get("token_focus", "ALL"),
                                 body.get("stop_loss_pct", 4), body.get("take_profit_pct", 10))
    elif name == "buy":
        ok, msg = demo.buy(addr, body.get("symbol", ""))
    elif name == "sell":
        ok, msg = demo.sell(addr, body.get("symbol", ""))
    elif name == "pause":
        ok, msg = demo.pause(addr)
    elif name == "withdraw":
        ok, msg = demo.withdraw(addr)
    elif name == "panic":
        ok, msg = demo.panic(addr)
    else:
        ok, msg = False, "ação desconhecida"
    return JSONResponse({"ok": ok, "detail": msg})


def make_soon(path):  # noqa: ANN001
    async def handler(request):  # noqa: ANN001
        return _resp(request, "placeholder.html", path, SOON[path])
    return handler


routes = [Route("/", home), Route("/style", style), Route("/docs", docs_page),
          Route("/guides", guides_page), Route("/live", live_page),
          Route("/api/live", api_live), Route("/console", console_page),
          Route("/api/auth/nonce", auth_nonce, methods=["POST"]),
          Route("/api/auth/verify", auth_verify, methods=["POST"]),
          Route("/api/auth/logout", auth_logout, methods=["POST"]),
          Route("/api/console/state", console_state),
          Route("/api/console/{name}", console_action, methods=["POST"])]
for p in SOON:
    routes.append(Route(p, make_soon(p)))
routes.append(Mount("/static", StaticFiles(directory=str(WEB / "static")), name="static"))

app = Starlette(routes=routes)

if __name__ == "__main__":
    print("Preview da web em  ->  http://localhost:8090   (Ctrl+C para parar)")
    uvicorn.run(app, host="127.0.0.1", port=8090, log_level="warning")

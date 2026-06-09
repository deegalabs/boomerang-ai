"""Preview LOCAL da web do Boomerang AI (Fase 0) — porta 8090.

Serve a fundação (design system + shell) SEM tocar no agente que roda na 8080.
Rotas não-construídas mostram um placeholder com o mesmo shell.

Uso: .venv\\Scripts\\python scripts\\preview_web.py   → http://localhost:8090
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

from boomerang.webapp.i18n import docs_nav, nav_items, pick_lang, strings

WEB = Path(__file__).resolve().parent.parent / "boomerang" / "webapp"
templates = Jinja2Templates(directory=str(WEB / "templates"))

# Placeholders bilíngues para rotas que vêm nas próximas fases.
SOON = {
    "/live":   {"phase": {"en": "Phase 3", "pt": "Fase 3"}, "big": {"en": "Live proof", "pt": "Prova ao vivo"},
                "soon": {"en": "Public read-only panel: equity, PnL and on-chain trades.",
                          "pt": "Painel público só-leitura: patrimônio, PnL e trades on-chain."}},
    "/console": {"phase": {"en": "Phase 4", "pt": "Fase 4"}, "big": {"en": "Console", "pt": "Console"},
                 "soon": {"en": "Owner control: connect wallet, configure, fund and trade.",
                           "pt": "Controle do dono: conectar carteira, configurar, fundear e operar."}},
}


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


def make_soon(path):  # noqa: ANN001
    async def handler(request):  # noqa: ANN001
        return _resp(request, "placeholder.html", path, SOON[path])
    return handler


routes = [Route("/", home), Route("/style", style), Route("/docs", docs_page),
          Route("/guides", guides_page)]
for p in SOON:
    routes.append(Route(p, make_soon(p)))
routes.append(Mount("/static", StaticFiles(directory=str(WEB / "static")), name="static"))

app = Starlette(routes=routes)

if __name__ == "__main__":
    print("Preview da web em  ->  http://localhost:8090   (Ctrl+C para parar)")
    uvicorn.run(app, host="127.0.0.1", port=8090, log_level="warning")

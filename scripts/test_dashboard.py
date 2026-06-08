"""Testa o dashboard (API + pagina) sem abrir porta, via Starlette TestClient."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import _isolate_state  # noqa: F401  isola o estado num tempdir ANTES de importar boomerang

from starlette.testclient import TestClient

from boomerang import persistence
from boomerang.webapp.server import make_app

TOKEN = "tok_test_123"


def main() -> int:
    # estado + trades de exemplo
    persistence.save_state({
        "state": "SCANNING", "equity_usd": 7.85, "peak_equity": 8.01, "drawdown_pct": 2.0,
        "stop_loss_pct": 4.0, "mode": "conservative",
        "token_focus": ["ETH", "SHIB"], "agent_address": "0xc72a37f4bb7c454Fd8a9EB629aFaEeb101F67dff",
        "positions": [],
    })
    persistence.append_trade({"type": "open", "symbol": "ETH", "amount_usd": 2.0,
                              "entry_price": 1634.0, "tx": "0x3457", "ts": time.time()})
    persistence.append_trade({"type": "close", "symbol": "ETH", "reason": "SELL_STOP_LOSS",
                              "pnl_pct": -4.0, "tx": "0xb8e7", "ts": time.time()})

    c = TestClient(make_app(TOKEN))

    assert c.get("/api/status?key=wrong").status_code == 403
    print("[auth] token errado -> 403  OK")

    s = c.get(f"/api/status?key={TOKEN}")
    assert s.status_code == 200 and s.json()["equity_usd"] == 7.85
    print(f"[api/status] equity={s.json()['equity_usd']} estado={s.json()['state']}  OK")

    t = c.get(f"/api/trades?key={TOKEN}")
    assert t.status_code == 200 and len(t.json()) >= 2
    print(f"[api/trades] {len(t.json())} trades  OK")

    d = c.get(f"/dash?key={TOKEN}")
    assert d.status_code == 200 and "Boomerang AI" in d.text
    assert c.get("/dash?key=wrong").status_code == 403
    print("[/dash] pagina servida c/ token, bloqueada sem token  OK")

    print("\n[PASS] Dashboard (API + pagina + auth por token) funcionando.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

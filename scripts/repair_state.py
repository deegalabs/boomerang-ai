"""Reconstrói state/agent_state.json a partir da REALIDADE on-chain.

Usado após a suíte de testes ter sobrescrito o estado real. Lê o saldo on-chain
de ADA (posição real aberta na demo) e remonta o estado coerente, com a config
de produção (15 tokens, stop 4%, conservador). Se não houver ADA, grava SCANNING.
"""
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from dotenv import load_dotenv
from web3 import Web3

from boomerang import persistence
from boomerang.config import load_config
from boomerang.vault.bnb_validation import BNBValidator
from boomerang.vault.twak_executor import TwakExecutor

load_dotenv()

ADA_ENTRY = 0.16739274645052518  # preço de entrada registrado na compra real (demo)


def main() -> int:
    cfg = load_config()
    v = BNBValidator(cfg)
    ex = TwakExecutor(cfg)
    v.set_quoter(ex)
    addr = ex.get_address("bsc")
    toks = json.loads((Path("data/eligible_tokens.json")).read_text())["tokens"]
    ada = Web3.to_checksum_address(toks["ADA"])

    qty = v._token_balance(ada, addr) / (10 ** v._decimals(ada))
    equity = float(v.wallet_breakdown(addr).get("total_usd") or 0.0)
    focus = list(cfg.user.get("token_focus", []))

    base = {
        "stop_loss_pct": 4.0, "mode": "conservative",
        "token_focus": focus, "peak_equity": round(equity, 4),
        "equity_usd": round(equity, 4), "drawdown_pct": 0.0,
        "last_trade_ts": time.time(), "agent_address": addr,
    }

    if qty > 0.01:
        stop = ADA_ENTRY * (1 - 4.0 / 100.0)
        pos = {
            "symbol": "ADA", "token_address": ada, "entry_price": ADA_ENTRY,
            "amount_usd": 2.0, "qty": qty, "stop_loss_price": stop,
            "trailing_active": False, "peak_price": ADA_ENTRY,
            "opened_at": time.time(), "tx_hash": None,
        }
        state = {**base, "state": "IN_POSITION", "positions": [pos]}
        print(f"Reconstruindo posição ADA: qty={qty:.6f} entry=${ADA_ENTRY:.6f} stop=${stop:.6f}")
    else:
        state = {**base, "state": "SCANNING", "positions": []}
        print("Sem ADA on-chain — gravando estado SCANNING limpo.")

    persistence.save_state(state)
    print(f"Estado gravado. equity=${equity:.2f} foco={len(focus)} tokens estado={state['state']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

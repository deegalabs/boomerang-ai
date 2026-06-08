"""Testa o livro-caixa do PaperExecutor (sem fundos, sem rede)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boomerang.config import load_config
from boomerang.vault.paper_executor import PaperExecutor

TOKEN = "0x2170Ed0880ac9A755fd29B2688956BD959F933F8"


class FakeValidator:
    def __init__(self, price): self.price = price
    def onchain_price_usd(self, token): return self.price


def main() -> int:
    cfg = load_config()
    fv = FakeValidator(100.0)
    pe = PaperExecutor(cfg, fv, starting_cash_usd=100.0)

    assert pe.portfolio_usd() == 100.0
    r = pe.buy(to_token=TOKEN, amount_usd=10.0)
    assert r.ok and abs(r.qty - 0.1) < 1e-9 and r.entry_price == 100.0
    # cash 90 + 0.1*100 = 100
    assert abs(pe.portfolio_usd() - 100.0) < 1e-6
    print(f"[paper] compra $10 -> qty {r.qty}, portfolio {pe.portfolio_usd()}  OK")

    fv.price = 110.0  # token sobe 10%
    assert abs(pe.portfolio_usd() - 101.0) < 1e-6  # 90 + 0.1*110
    print(f"[paper] preco +10% -> portfolio {pe.portfolio_usd()} (lucro nao realizado)  OK")

    s = pe.sell_all(token=TOKEN, amount=r.qty)
    assert s.ok and abs(pe.portfolio_usd() - 101.0) < 1e-6  # cash 90 + 11
    print(f"[paper] vende tudo @110 -> portfolio {pe.portfolio_usd()}  OK")

    w = pe.transfer_to_owner(to="0xowner", amount=50.0, token="USDT")
    assert w["paper"] and abs(pe.portfolio_usd() - 51.0) < 1e-6
    print(f"[paper] saque $50 -> portfolio {pe.portfolio_usd()}  OK")

    print("\n[PASS] PaperExecutor: livro-caixa simulado correto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

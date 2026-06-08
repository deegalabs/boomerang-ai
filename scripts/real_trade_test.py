"""PRIMEIRO TRADE REAL — valida a execucao on-chain via twak (gasta dinheiro real).

Faz um swap seguro USDT->USDC (~$1) na BSC: risco de mercado ~zero (stable<->stable),
mas exercita o caminho REAL (twak swap + parse de tx hash/preco/qty). Use isto para
confirmar a execucao antes de ligar o agente no modo real.

Pre-requisito: carteira do agente com USDT + BNB (gas) na BSC.
Uso:
  .venv\\Scripts\\python scripts\\real_trade_test.py            # so checa saldos
  .venv\\Scripts\\python scripts\\real_trade_test.py --confirm  # executa o swap real
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boomerang.config import load_config
from boomerang.vault.twak_executor import TwakError, TwakExecutor


def main() -> int:
    cfg = load_config()
    ex = TwakExecutor(cfg)
    pw = cfg.secrets.wallet_password or ""
    usdt = cfg.network["usdt_bsc_address"]
    usdc = cfg.network["usdc_bsc_address"]
    agent = "0xc72a37f4bb7c454Fd8a9EB629aFaEeb101F67dff"

    print("== Saldos na BSC (agente) ==")
    try:
        bal_usdt = ex._run(["balance", "--address", agent, "--chain", "bsc", "--token", usdt])
        print("  USDT:", bal_usdt.get("available") if isinstance(bal_usdt, dict) else bal_usdt)
    except TwakError as e:
        print("  USDT: erro", e)
    try:
        bal_bnb = ex._run(["balance", "--address", agent, "--chain", "bsc", "--coin", "714"])
        print("  BNB (gas):", bal_bnb.get("available") if isinstance(bal_bnb, dict) else bal_bnb)
    except TwakError as e:
        print("  BNB: erro", e)

    if "--confirm" not in sys.argv:
        print("\n(So checagem. Rode com --confirm para executar o swap real de ~$1.)")
        return 0

    print("\n== Executando swap REAL USDT -> USDC (~$1) ==")
    res = ex.buy(to_token=usdc, amount_usd=1.05, password=pw)
    if res.ok:
        print(f"  [OK] tx={res.tx_hash} | entry_price={res.entry_price} | qty={res.qty}")
        print("  -> Execucao real validada. Confira os campos parseados acima.")
        return 0
    print(f"  [FALHOU] {res.error}")
    print("  (Se for 'insufficient funds/gas', falta USDT ou BNB na BSC.)")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

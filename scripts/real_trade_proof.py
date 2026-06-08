"""PROVA ON-CHAIN: trade real pequeno numa moeda antes bloqueada (default ADA).

Faz o caminho REAL do bot: valida (Filtro 2 via agregador) -> COMPRA real via TWAK
-> confirma saldo on-chain -> REVENDE para USDC -> confirma. Mostra os tx hashes.

Uso: .venv\\Scripts\\python scripts\\real_trade_proof.py [SYMBOL] [USD]
Gasta dinheiro REAL (~$2 + gás). Requer .env (WALLET_PASSWORD etc.).
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

from boomerang.config import load_config
from boomerang.vault.bnb_validation import BNBValidator
from boomerang.vault.twak_executor import TwakError, TwakExecutor

load_dotenv()
SCAN = "https://bscscan.com/tx/"


def main() -> int:
    symbol = (sys.argv[1] if len(sys.argv) > 1 else "ADA").upper()
    usd = float(sys.argv[2]) if len(sys.argv) > 2 else 2.0
    cfg = load_config()
    pw = os.getenv("WALLET_PASSWORD", "")
    toks = json.loads((Path("data/eligible_tokens.json")).read_text())["tokens"]
    if symbol not in toks:
        print(f"[ERRO] {symbol} não está na whitelist.")
        return 1
    addr = Web3.to_checksum_address(toks[symbol])

    val = BNBValidator(cfg)
    ex = TwakExecutor(cfg)
    val.set_quoter(ex)
    holder = ex.get_address("bsc")
    print(f"Carteira do agente: {holder}")
    print(f"Alvo: {symbol} ({addr}) | valor: ${usd}\n")

    def bal(a):  # saldo humano on-chain
        try:
            return val._token_balance(a, holder) / (10 ** val._decimals(a))
        except Exception:
            return 0.0

    usdc = Web3.to_checksum_address(cfg.network["usdc_bsc_address"])
    print("Saldos ANTES:")
    print(f"  USDC: {bal(usdc):.6f} | {symbol}: {bal(addr):.8f}\n")

    # 1) FILTRO 2 (via agregador V2+V3)
    print("[1/3] Filtro 2 (validação via agregador)...")
    v = val.validate(symbol=symbol, token_address=addr, amount_usd=usd)
    print(f"      -> {'APROVADO' if v.ok else 'BARRADO'} | {v.detail}")
    if not v.ok:
        return 2

    # 2) COMPRA real
    print(f"\n[2/3] COMPRANDO ${usd} de {symbol} (real, assinando via TWAK)...")
    try:
        buy = ex.buy(to_token=addr, amount_usd=usd, password=pw)
    except TwakError as e:
        print(f"      [ERRO] {e}")
        return 3
    if not buy.ok:
        print(f"      [FALHOU] {buy.error}")
        return 3
    print(f"      -> OK | tx: {SCAN}{buy.tx_hash}")
    print(f"      -> recebido (parse): {buy.qty} {symbol}")
    time.sleep(4)
    held = bal(addr)
    print(f"      -> saldo on-chain de {symbol}: {held:.8f}")

    # 3) REVENDA real (volta para USDC)
    if held <= 0:
        print("\n[3/3] Sem saldo para revender (verifique a tx de compra).")
        return 0
    print(f"\n[3/3] REVENDENDO {held:.8f} {symbol} -> USDC (real)...")
    try:
        sell = ex.sell_all(token=addr, amount=held, password=pw)
    except TwakError as e:
        print(f"      [ERRO] {e}")
        return 4
    if not sell.ok:
        print(f"      [FALHOU] {sell.error}")
        return 4
    print(f"      -> OK | tx: {SCAN}{sell.tx_hash}")
    time.sleep(4)

    print("\nSaldos DEPOIS:")
    print(f"  USDC: {bal(usdc):.6f} | {symbol}: {bal(addr):.8f}")
    print("\n[PROVA COMPLETA] compra e venda reais executadas on-chain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

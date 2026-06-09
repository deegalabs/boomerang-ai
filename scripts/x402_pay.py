"""Faz UMA chamada paga real à CoinMarketCap via x402 (assinada pelo BNB AI Agent SDK).

Prova o pagamento pay-per-call ponta a ponta: desafio 402 -> assina EIP-3009 ->
reenvia -> dados. A carteira pagadora precisa deter o ativo (padrão: USDC na Base).

Uso:
  python scripts/x402_pay.py                         # carteira de identidade, BNB quote
  python scripts/x402_pay.py --tool get_crypto_latest_news --symbol ETH
  python scripts/x402_pay.py --network eip155:56     # pagar na BNB Chain (permit2)

Pré-checagem de saldo: avisa (mas tenta mesmo assim) se a carteira tiver 0 do ativo.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

from boomerang.identity.bnb_agent import IDENTITY_DIR, _password
from boomerang.payments import x402_cmc

load_dotenv()

_RPC = {"eip155:8453": "https://mainnet.base.org", "eip155:56": "https://bsc-dataseed.binance.org"}
_DEC = {x402_cmc.USDC_BASE: 6, x402_cmc.USDC_BSC: 18, x402_cmc.UNITED_STABLES_BSC: 18}


def _balance(network: str, asset: str, addr: str) -> float | None:
    rpc = _RPC.get(network)
    if not rpc:
        return None
    try:
        from web3 import Web3
        w3 = Web3(Web3.HTTPProvider(rpc))
        abi = [{"constant": True, "inputs": [{"name": "a", "type": "address"}],
                "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"}]
        c = w3.eth.contract(address=w3.to_checksum_address(asset), abi=abi)
        raw = c.functions.balanceOf(w3.to_checksum_address(addr)).call()
        return raw / (10 ** _DEC.get(asset, 6))
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tool", default="get_crypto_quotes_latest")
    ap.add_argument("--symbol", default="BNB")
    ap.add_argument("--network", default="eip155:8453", help="eip155:8453 (Base) | eip155:56 (BNB)")
    args = ap.parse_args()

    signer = x402_cmc.open_signer(_password(), str(IDENTITY_DIR))
    addr = signer.wallet_address
    asset = x402_cmc.USDC_BASE if args.network == "eip155:8453" else x402_cmc.USDC_BSC
    bal = _balance(args.network, asset, addr)
    print(f"Carteira pagadora: {addr}")
    if bal is not None:
        print(f"Saldo do ativo de pagamento ({args.network}): {bal:.4f}")
        if bal <= 0:
            print("  AVISO: saldo 0 -> a liquidacao vai reverter. Funde a carteira primeiro.")

    print(f"Chamando {args.tool}({args.symbol}) via x402...")
    out = x402_cmc.call_tool(args.tool, {"symbol": args.symbol}, signer, prefer_network=args.network)
    print(f"paid={out['paid']} status={out['status']} amount={out.get('amount')} {out.get('network','')}")
    if out["status"] == 200:
        res = out.get("result", {})
        text = str(res)
        print("PAGAMENTO LIQUIDADO. Dados recebidos (trecho):")
        print(text[:600])
        return 0
    print("Nao liquidou. Resposta da CMC:")
    print(out.get("error", "")[:500])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

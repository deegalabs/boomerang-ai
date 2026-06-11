"""Constrói data/eligible_tokens.json a partir da lista oficial dos 149 símbolos
elegíveis, resolvendo o endereço BEP-20 (BSC) de cada um via CMC /info.

Fonte autoritativa = CMC (a elegibilidade é "BEP-20 listados na CoinMarketCap").
Tokens sem endereço BSC são pulados (não são tradáveis na PancakeSwap de qualquer forma).
"""
from __future__ import annotations

import json
import os
import time

import httpx
from dotenv import load_dotenv

load_dotenv()
KEY = os.getenv("CMC_API_KEY")
BASE = "https://pro-api.coinmarketcap.com"
H = {"X-CMC_PRO_API_KEY": KEY, "Accept": "application/json"}

# Lista oficial (149). Símbolos duplicados/variações de caixa são deduplicados.
RAW = """ETH USDT USDC XRP TRX DOGE ZEC ADA LINK BCH DAI TON USD1 USDe M LTC AVAX SHIB
XAUt WLFI H DOT UNI ASTER DEXE USDD ETC AAVE ATOM U STABLE FIL INJ NIGHT FET TUSD BONK
PENGU CAKE SIREN LUNC ZRO KITE FDUSD BEAT PIEVERSE BTT NFT EDGE FLOKI LDO B FF PENDLE
NEX STG AXS TWT HOME RAY COMP GWEI XCN GENIUS XPL BAT SKYAI APE IP SFP TAG NXPC AB SAHARA
1INCH CHEEMS BANANAS31 RIVER MYX RAVE SNX FORM LAB HTX USDf CTM BDX SLX UB DUCKY FRAX BILL
WFI KOGE ALE FRXUSD USDF GOMINING VCNT GUA DUSD SMILEK 0G BEAM MY SOON REAL Q AIOZ ZIG YFI
TAC lisUSD CYS ZAMA TRIA HUMA PLUME ZIL XPR ZETA BabyDoge NILA ROSE VELO UAI BRETT OPEN BSB
TOSHI BAS ACH AXL LUR ELF KAVA APR IRYS EURI XUSD BARD DUSK SUSHI PEAQ COAI BDCA XAUM""".split()

# símbolos não-ASCII / problemáticos da lista (informados à parte p/ não quebrar a URL)
EXTRA = ["币安人生"]
SYMBOLS = list(dict.fromkeys([s for s in RAW] + EXTRA))  # dedupe preservando ordem


def bsc_addr_for(entries) -> str | None:
    """Acha o endereço na BNB Smart Chain entre as plataformas do(s) token(s)."""
    items = entries if isinstance(entries, list) else [entries]
    for ent in items:
        for ca in (ent.get("contract_address") or []):
            plat = ca.get("platform") or {}
            name = (plat.get("name") or "").lower()
            coin = ((plat.get("coin") or {}).get("symbol") or "").lower()
            if "bnb smart chain" in name or "binance smart chain" in name or coin == "bnb":
                return ca.get("contract_address")
    return None


def main() -> None:
    found: dict[str, str] = {}
    missing: list[str] = []
    for i in range(0, len(SYMBOLS), 25):
        batch = SYMBOLS[i:i + 25]
        try:
            r = httpx.get(f"{BASE}/v2/cryptocurrency/info",
                          params={"symbol": ",".join(batch)}, headers=H, timeout=30)
            data = r.json().get("data", {}) if r.status_code == 200 else {}
        except Exception as exc:  # noqa: BLE001
            print("batch falhou:", exc); data = {}
        for sym in batch:
            entries = data.get(sym) or data.get(sym.upper())
            addr = bsc_addr_for(entries) if entries else None
            if addr:
                found[sym.upper()] = httpx_checksum(addr)
            else:
                missing.append(sym)
        time.sleep(1.0)
    print(f"\nRESOLVIDOS: {len(found)} | SEM ENDEREÇO BSC: {len(missing)}")
    print("Sem BSC:", ", ".join(missing))
    out = {
        "_note": "149 tokens elegiveis (BEP-20 na CoinMarketCap). Endereco BSC via CMC /info.",
        "_status": f"{len(found)} com endereco BSC de {len(SYMBOLS)} simbolos.",
        "base": {"USDC": "0x8AC76a51cc950d9822D68b83fE1Ad97B32Cd580d",
                 "USDT": "0x55d398326f99059fF775485246999027B3197955"},
        "tokens": dict(sorted(found.items())),
    }
    with open("data/eligible_tokens.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print("escrito data/eligible_tokens.json")


def httpx_checksum(addr: str) -> str:
    try:
        from web3 import Web3
        return Web3.to_checksum_address(addr)
    except Exception:  # noqa: BLE001
        return addr


if __name__ == "__main__":
    main()

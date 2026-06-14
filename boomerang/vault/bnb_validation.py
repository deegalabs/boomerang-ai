"""Filter 2 — On-chain physical validation on the BNB Smart Chain.

Receives a signal approved by Filter 1 and, BEFORE any signature, validates:
  1. Whitelist  — does the token belong to the 149 eligible ones? (otherwise it doesn't count in PnL)
  2. Liquidity/Slippage — simulates the buy on PancakeSwap (getAmountsOut, read-only)
  3. Hidden tax — round-trip heuristic detects a token with fee-on-transfer/burn
  4. Oracle desync — on-chain price vs CMC price

All via `view` calls (eth_call) → ZERO gas cost. A failure closes the trade.

Known limits (v1): the round-trip check catches fee-on-transfer/tax via
reserve math; a honeypot that REVERTS on the real sell needs a swap
simulation with state-override (future hardening). Since the focus is on
liquid blue-chip tokens, the residual risk is low.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from web3 import Web3

from boomerang.config import Config
from boomerang.types import RejectReason, ValidationResult

ROOT = Path(__file__).resolve().parent.parent.parent

# Minimal ABIs (only what is needed) ──────────────────────────────────────────
_ROUTER_ABI = json.loads(
    '[{"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},'
    '{"internalType":"address[]","name":"path","type":"address[]"}],'
    '"name":"getAmountsOut",'
    '"outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],'
    '"stateMutability":"view","type":"function"}]'
)
_ERC20_ABI = json.loads(
    '[{"inputs":[],"name":"decimals","outputs":[{"internalType":"uint8","name":"","type":"uint8"}],'
    '"stateMutability":"view","type":"function"},'
    '{"inputs":[],"name":"symbol","outputs":[{"internalType":"string","name":"","type":"string"}],'
    '"stateMutability":"view","type":"function"},'
    '{"inputs":[{"internalType":"address","name":"account","type":"address"}],"name":"balanceOf",'
    '"outputs":[{"internalType":"uint256","name":"","type":"uint256"}],'
    '"stateMutability":"view","type":"function"}]'
)
# PancakeSwap V2 Factory — getPair to measure the depth (liquidity) of the token/WBNB pool.
_V2_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
_FACTORY_ABI = json.loads(
    '[{"inputs":[{"internalType":"address","name":"","type":"address"},'
    '{"internalType":"address","name":"","type":"address"}],"name":"getPair",'
    '"outputs":[{"internalType":"address","name":"","type":"address"}],'
    '"stateMutability":"view","type":"function"}]'
)
_PAIR_ABI = json.loads(
    '[{"inputs":[],"name":"getReserves","outputs":['
    '{"internalType":"uint112","name":"_reserve0","type":"uint112"},'
    '{"internalType":"uint112","name":"_reserve1","type":"uint112"},'
    '{"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}],'
    '"stateMutability":"view","type":"function"},'
    '{"inputs":[],"name":"token0","outputs":[{"internalType":"address","name":"","type":"address"}],'
    '"stateMutability":"view","type":"function"}]'
)
# PancakeSwap V3 — QuoterV2 (price/liquidity on V3, which the V2 router cannot see).
_V3_QUOTER = "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997"
_V3_FEE_TIERS = (500, 2500, 100, 10000)  # ordered by probability of having a pool
_V3_QUOTER_ABI = json.loads(
    '[{"inputs":[{"components":[{"internalType":"address","name":"tokenIn","type":"address"},'
    '{"internalType":"address","name":"tokenOut","type":"address"},'
    '{"internalType":"uint256","name":"amountIn","type":"uint256"},'
    '{"internalType":"uint24","name":"fee","type":"uint24"},'
    '{"internalType":"uint160","name":"sqrtPriceLimitX96","type":"uint160"}],'
    '"internalType":"struct IQuoterV2.QuoteExactInputSingleParams","name":"params","type":"tuple"}],'
    '"name":"quoteExactInputSingle","outputs":['
    '{"internalType":"uint256","name":"amountOut","type":"uint256"},'
    '{"internalType":"uint160","name":"sqrtPriceX96After","type":"uint160"},'
    '{"internalType":"uint32","name":"initializedTicksCrossed","type":"uint32"},'
    '{"internalType":"uint256","name":"gasEstimate","type":"uint256"}],'
    '"stateMutability":"nonpayable","type":"function"}]'
)
# A round-trip (buy+sell) must retain >= this; below = illiquid/honeypot/tax.
_MIN_ROUNDTRIP = 0.97

# PancakeSwap V2 fee per hop = 0.25% → retention factor per hop.
_PCS_FEE_PER_HOP = 0.0025

# WBNB (wrapped BNB) — used only to price the NATIVE gas balance (BNB) in USDT.
_WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
# Balances below this value in USD are ignored (dust/rounding).
_DUST_USD = 0.01


class BNBValidator:
    def __init__(self, config: Config, logger: logging.Logger | None = None) -> None:
        self._cfg = config
        self._log = logger or logging.getLogger("boomerang.vault.bnb")
        self.w3 = Web3(Web3.HTTPProvider(config.bsc_rpc_url, request_kwargs={"timeout": 20}))

        self._router = self.w3.eth.contract(
            address=Web3.to_checksum_address(config.network["pancakeswap_router_v2"]),
            abi=_ROUTER_ABI,
        )
        self._v3_quoter = self.w3.eth.contract(
            address=Web3.to_checksum_address(_V3_QUOTER), abi=_V3_QUOTER_ABI,
        )
        self._factory = self.w3.eth.contract(
            address=Web3.to_checksum_address(_V2_FACTORY), abi=_FACTORY_ABI,
        )
        self._executor = None  # aggregator quoter (TWAK); injected via set_quoter()
        self._usdt = Web3.to_checksum_address(config.network["usdt_bsc_address"])
        self._wbnb = Web3.to_checksum_address(_WBNB)
        try:
            self._usdc = Web3.to_checksum_address(config.network["usdc_bsc_address"])
        except (KeyError, TypeError):
            self._usdc = None
        self._decimals_cache: dict[str, int] = {}
        self._whitelist = self._load_whitelist()
        self._token_map = self._load_token_map()  # symbol -> address (tokens only, no base)

    # ── connectivity ─────────────────────────────────────────────────────────
    def is_connected(self) -> bool:
        try:
            return self.w3.is_connected() and self.w3.eth.chain_id == int(self._cfg.network["bsc_chain_id"])
        except Exception as exc:  # noqa: BLE001
            self._log.error("RPC connection failure: %s", exc)
            return False

    # ── whitelist ────────────────────────────────────────────────────────────
    def _load_whitelist(self) -> set[str]:
        path = ROOT / self._cfg.hackathon["eligible_tokens_file"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._log.warning("eligible_tokens.json not found — empty whitelist (closes everything).")
            return set()
        addrs = {Web3.to_checksum_address(a) for a in data.get("tokens", {}).values()}
        if not addrs:
            self._log.warning("Whitelist EMPTY — populate the 149 tokens in Phase 0. Trades blocked.")
        return addrs

    def _load_token_map(self) -> dict[str, str]:
        """symbol -> address (checksum) of the focus tokens (without the base stable)."""
        path = ROOT / self._cfg.hackathon["eligible_tokens_file"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        out: dict[str, str] = {}
        bases = {"USDC", "USDT"}  # base stables enter as 'stable'; don't duplicate as 'token'
        for sym, addr in data.get("tokens", {}).items():
            if sym.upper() in bases:
                continue
            try:
                out[sym] = Web3.to_checksum_address(addr)
            except Exception:  # noqa: BLE001
                continue
        return out

    def is_whitelisted(self, token_address: str) -> bool:
        return Web3.to_checksum_address(token_address) in self._whitelist

    # ── on-chain helpers ─────────────────────────────────────────────────────
    def _decimals(self, token_address: str) -> int:
        addr = Web3.to_checksum_address(token_address)
        if addr not in self._decimals_cache:
            erc20 = self.w3.eth.contract(address=addr, abi=_ERC20_ABI)
            self._decimals_cache[addr] = int(erc20.functions.decimals().call())
        return self._decimals_cache[addr]

    def _amounts_out(self, amount_in: int, path: list[str]) -> list[int]:
        checksum_path = [Web3.to_checksum_address(p) for p in path]
        return self._router.functions.getAmountsOut(amount_in, checksum_path).call()

    def set_quoter(self, executor) -> None:  # noqa: ANN001
        """Injects the TWAK executor so Filter 2 validates via the REAL route (aggregator)."""
        self._executor = executor

    # ── V3 quote (single-hop, best fee tier) ─────────────────────────────────
    def _v3_out(self, token_in: str, token_out: str, amount_in: int) -> int:
        ti = Web3.to_checksum_address(token_in)
        to = Web3.to_checksum_address(token_out)
        best = 0
        for fee in _V3_FEE_TIERS:
            try:
                out = self._v3_quoter.functions.quoteExactInputSingle(
                    (ti, to, int(amount_in), fee, 0)).call()[0]
                if out > best:
                    best = out
            except Exception:  # noqa: BLE001 — no pool in this tier reverts
                continue
        return best

    def _route_out(self, token_in: str, token_out: str, amount_in: int) -> int:
        """Best output between V2 (direct/via WBNB) and V3 (direct/via WBNB).

        Stops at the 1st route with liquidity (the probe is small → price ~equal in any
        pool). Covers tokens whose liquidity migrated to V3 that V2 cannot see.
        """
        ti = Web3.to_checksum_address(token_in)
        to = Web3.to_checksum_address(token_out)
        wbnb = self._wbnb
        # 1) V2 direto
        try:
            out = self._amounts_out(amount_in, [ti, to])[-1]
            if out > 0:
                return out
        except Exception:  # noqa: BLE001
            pass
        # 2) V3 direto
        out = self._v3_out(ti, to, amount_in)
        if out > 0:
            return out
        if ti != wbnb and to != wbnb:
            # 3) V2 via WBNB
            try:
                out = self._amounts_out(amount_in, [ti, wbnb, to])[-1]
                if out > 0:
                    return out
            except Exception:  # noqa: BLE001
                pass
            # 4) V3 ti->WBNB, depois WBNB->to (V2 ou V3)
            ow = self._v3_out(ti, wbnb, amount_in)
            if ow > 0:
                try:
                    return self._amounts_out(ow, [wbnb, to])[-1]
                except Exception:  # noqa: BLE001
                    leg = self._v3_out(wbnb, to, ow)
                    if leg > 0:
                        return leg
        return 0

    # ── on-chain spot price (USD per 1 token) — now covers V2 AND V3 ─────────
    def onchain_price_usd(self, token_address: str) -> float:
        """Price of 1 token in USD, probing with ~1 USDT (impact ~0), via V2 or V3.

        Probes 'how many tokens per 1 USDT' and inverts → robust for expensive and
        cheap tokens. Raises ValueError if no route has liquidity."""
        token = Web3.to_checksum_address(token_address)
        usdt_dec = self._decimals(self._usdt)
        probe = 10 ** usdt_dec  # ~1 USDT
        tokens_raw = self._route_out(self._usdt, token, probe)
        if tokens_raw <= 0:
            raise ValueError(f"No price route (V2/V3) for {token}.")
        tokens = tokens_raw / (10 ** self._decimals(token))
        price = 1.0 / tokens if tokens > 0 else 0.0
        # Sanity: the direct V2/V3 probe gives garbage prices on thin-liquidity tokens
        # (e.g., ATOM came out at $5927; on some nodes, millions), inflating equity and risk.
        # No eligible token exceeds ~$2k (ETH). Above $5k = broken probe:
        # treat as NO price (wallet_breakdown ignores it; it doesn't enter equity).
        if price <= 0 or price > 5000:
            raise ValueError(f"Implausible on-chain price for {token}: ${price:.2f}")
        return price

    def wbnb_pool_liquidity_usd(self, token_address: str) -> float:
        """SKILL Size by liquidity: depth of the token/WBNB pool (V2) in USD.

        Reads the pair reserves on PancakeSwap V2 and converts the WBNB side to USD. It is
        the direct measure of how deep/safe the token's on-chain market is. Returns 0.0
        if there is no pair (or on error) — the caller treats it as illiquid."""
        try:
            token = Web3.to_checksum_address(token_address)
            pair = self._factory.functions.getPair(token, self._wbnb).call()
            if int(pair, 16) == 0:
                return 0.0
            c = self.w3.eth.contract(address=Web3.to_checksum_address(pair), abi=_PAIR_ABI)
            r0, r1, _ = c.functions.getReserves().call()
            token0 = Web3.to_checksum_address(c.functions.token0().call())
            wbnb_reserve = (r0 if token0 == self._wbnb else r1) / 1e18
            bnb_price = self._price_via([self._wbnb, self._usdt])  # USDT per 1 BNB
            return wbnb_reserve * bnb_price
        except Exception as exc:  # noqa: BLE001
            self._log.debug("Pool liquidity unavailable for %s: %s", token_address, exc)
            return 0.0

    def _token_balance(self, token_address: str, holder: str) -> int:
        erc20 = self.w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=_ERC20_ABI)
        return int(erc20.functions.balanceOf(Web3.to_checksum_address(holder)).call())

    def _price_via(self, path: list[str]) -> float:
        """Price of 1 unit of the 1st token in the path in USDT (size-1 quote)."""
        first = Web3.to_checksum_address(path[0])
        one = 10 ** self._decimals(first)
        out = self._amounts_out(one, path)
        return out[-1] / (10 ** self._decimals(self._usdt))

    # ── wallet composition (real on-chain balances → USD) ─────────────────────
    def wallet_breakdown(self, address: str) -> dict:
        """Reads the REAL on-chain wallet balances and converts each coin to USD.

        Read-only (get_balance / balanceOf via eth_call) — zero gas cost and
        does NOT touch the private key. Returns:
            {address, total_usd, holdings:[{symbol, kind, balance, price_usd, value_usd, pct}]}
        sorted by descending value, ignoring dust (< $0.01).
        """
        if not address:
            return {"address": None, "total_usd": 0.0, "holdings": []}
        holder = Web3.to_checksum_address(address)
        holdings: list[dict] = []
        total = 0.0

        def add(symbol: str, kind: str, balance: float, price: float) -> None:
            nonlocal total
            value = balance * price
            if value < _DUST_USD:
                return
            holdings.append({"symbol": symbol, "kind": kind, "balance": balance,
                             "price_usd": price, "value_usd": value})
            total += value

        # 1) Native BNB (gas reserve)
        try:
            bnb = self.w3.eth.get_balance(holder) / 1e18
            if bnb > 0:
                add("BNB", "gas", bnb, self._price_via([self._wbnb, self._usdt]))
        except Exception as exc:  # noqa: BLE001
            self._log.warning("BNB balance unavailable: %s", exc)

        # 2) Stables (operating base) — fixed price $1
        stables = {"USDT": self._usdt}
        if self._usdc:
            stables["USDC"] = self._usdc
        for sym, addr in stables.items():
            try:
                bal = self._token_balance(addr, holder) / (10 ** self._decimals(addr))
                add(sym, "stable", bal, 1.0)
            except Exception as exc:  # noqa: BLE001
                self._log.warning("%s balance unavailable: %s", sym, exc)

        # 3) Focus tokens (whitelist) with balance > 0
        for sym, addr in self._token_map.items():
            try:
                raw = self._token_balance(addr, holder)
                if raw <= 0:
                    continue
                bal = raw / (10 ** self._decimals(addr))
                add(sym, "token", bal, self.onchain_price_usd(addr))
            except Exception as exc:  # noqa: BLE001
                self._log.debug("Token %s without quote/balance: %s", sym, exc)

        for h in holdings:
            h["pct"] = (h["value_usd"] / total * 100.0) if total > 0 else 0.0
        holdings.sort(key=lambda h: h["value_usd"], reverse=True)
        return {"address": holder, "total_usd": total, "holdings": holdings}

    # ── full validation ──────────────────────────────────────────────────────
    def validate(
        self,
        *,
        symbol: str,
        token_address: str,
        amount_usd: float,
        cmc_price_usd: float | None = None,
    ) -> ValidationResult:
        """Filter 2. Whitelist (always) + liquidity/output check.

        If an aggregator quoter (TWAK) is injected, validates via the REAL ROUTE that
        execution will use (covers V2+V3+other DEXs). Otherwise falls back to V2 simulation (tests).
        """
        token = Web3.to_checksum_address(token_address)
        if not self.is_whitelisted(token):
            return ValidationResult(False, symbol, token, reason=RejectReason.NOT_WHITELISTED,
                                    detail="Token outside the 149 eligible ones.")
        if self._executor is not None:
            return self._validate_via_aggregator(
                symbol=symbol, token=token, amount_usd=amount_usd, cmc_price_usd=cmc_price_usd)
        return self._validate_v2(
            symbol=symbol, token_address=token, amount_usd=amount_usd, cmc_price_usd=cmc_price_usd)

    def _validate_via_aggregator(
        self, *, symbol: str, token: str, amount_usd: float,
        cmc_price_usd: float | None = None,
    ) -> ValidationResult:
        """Validates by buying and reselling via the TWAK quote (real round-trip)."""
        # SKILL Size by liquidity: is the pool deep enough? Protects against
        # shallow/manipulable markets and against a trade too big for the pool.
        liq = self.wbnb_pool_liquidity_usd(token)
        min_liq = self._cfg.min_pool_liquidity_usd
        if liq < min_liq:
            return ValidationResult(False, symbol, token, reason=RejectReason.NO_LIQUIDITY,
                                    detail=f"Shallow pool (~${liq:,.0f} < minimum ${min_liq:,.0f}).")
        cap = liq * self._cfg.max_pool_share_pct / 100.0
        if amount_usd > cap:
            return ValidationResult(False, symbol, token, reason=RejectReason.HIGH_SLIPPAGE,
                                    detail=f"Trade ${amount_usd:.2f} > {self._cfg.max_pool_share_pct:.0f}% "
                                           f"of the pool (~${liq:,.0f}); reduce the size.")
        base = self._cfg.dev_safety["base_stable_symbol"]  # e.g.: USDC
        try:
            buy = self._executor.quote(base, token, amount_usd, self._cfg.max_slippage_pct)
            tokens_out = float(buy.get("out") or 0.0)
            if tokens_out <= 0:
                return ValidationResult(False, symbol, token, reason=RejectReason.NO_LIQUIDITY,
                                        detail="Aggregator found no buy route.")
            sell = self._executor.quote(token, base, amount_usd, self._cfg.max_slippage_pct)
            usdc_back = float(sell.get("out") or 0.0)
        except Exception as exc:  # noqa: BLE001
            return ValidationResult(False, symbol, token, reason=RejectReason.NO_LIQUIDITY,
                                    detail=f"Aggregator quote failed: {exc}")

        # Round-trip: bought $X and resold — how much came back? (captures slippage+tax+illiquidity)
        retention = usdc_back / amount_usd if amount_usd > 0 else 0.0
        if retention < _MIN_ROUNDTRIP:
            perda = (1 - retention) * 100.0
            return ValidationResult(False, symbol, token, reason=RejectReason.BURNING_TAX,
                                    estimated_slippage_pct=perda,
                                    detail=f"Round-trip retains only {retention*100:.2f}% "
                                           f"(loss {perda:.2f}%) → illiquid/tax.")

        # Oracle: effective route price (USD per token) vs CMC price.
        divergence_pct = None
        route_price = amount_usd / tokens_out  # USDC spent per token received
        if cmc_price_usd and cmc_price_usd > 0:
            divergence_pct = abs(route_price - cmc_price_usd) / cmc_price_usd * 100.0
            if divergence_pct > self._cfg.oracle_divergence_max_pct:
                return ValidationResult(False, symbol, token, oracle_divergence_pct=divergence_pct,
                                        reason=RejectReason.ORACLE_DESYNC,
                                        detail=f"Divergence {divergence_pct:.2f}% "
                                               f"(route {route_price:.6f} vs CMC {cmc_price_usd:.6f}).")
        impact = buy.get("price_impact")
        slip = float(impact) if impact is not None else (1 - retention) * 100.0
        return ValidationResult(True, symbol, token, estimated_slippage_pct=slip,
                                oracle_divergence_pct=divergence_pct,
                                detail=f"Approved by Filter 2 (aggregator, round-trip {retention*100:.2f}%).")

    def _validate_v2(
        self,
        *,
        symbol: str,
        token_address: str,
        amount_usd: float,
        cmc_price_usd: float | None = None,
    ) -> ValidationResult:
        token = Web3.to_checksum_address(token_address)

        # 1) Whitelist (fail-closed)
        if not self.is_whitelisted(token):
            return ValidationResult(False, symbol, token, reason=RejectReason.NOT_WHITELISTED,
                                    detail="Token outside the 149 eligible ones.")

        usdt_dec = self._decimals(self._usdt)
        amount_in = int(amount_usd * (10 ** usdt_dec))

        try:
            # 2) Buy simulation USDT -> token
            buy = self._amounts_out(amount_in, [self._usdt, token])
            expected_out = buy[-1]
            if expected_out <= 0:
                return ValidationResult(False, symbol, token, reason=RejectReason.NO_LIQUIDITY,
                                        detail="Zero output in the simulation.")

            # price impact: effective rate vs reference rate (1 USDT)
            ref_in = 10 ** usdt_dec
            ref_out = self._amounts_out(ref_in, [self._usdt, token])[-1]
            rate_ref = ref_out / ref_in            # tokens per base unit of USDT (no impact)
            rate_eff = expected_out / amount_in     # tokens per base unit (our order)
            price_impact_pct = max((rate_ref - rate_eff) / rate_ref * 100.0, 0.0)

            if price_impact_pct > self._cfg.max_slippage_pct:
                return ValidationResult(False, symbol, token, estimated_slippage_pct=price_impact_pct,
                                        reason=RejectReason.HIGH_SLIPPAGE,
                                        detail=f"Impact {price_impact_pct:.3f}% > cap {self._cfg.max_slippage_pct}%.")

            # 3) Hidden tax (round-trip): sell back and measure retention
            sell_back = self._amounts_out(expected_out, [token, self._usdt])[-1]
            retention = sell_back / amount_in
            # expected without tax: two pool fees + 2x the price impact
            expected_retention = (1 - _PCS_FEE_PER_HOP) ** 2 * (1 - 2 * price_impact_pct / 100.0)
            # 1% tolerance for noise/rounding
            if retention < expected_retention - 0.01:
                perda = (1 - retention) * 100.0
                return ValidationResult(False, symbol, token, reason=RejectReason.BURNING_TAX,
                                        detail=f"Round-trip retains only {retention*100:.2f}% (loss {perda:.2f}%) → hidden tax.")

            # 4) Oracle desync (if CMC provided a price)
            divergence_pct = None
            if cmc_price_usd and cmc_price_usd > 0:
                onchain = self.onchain_price_usd(token)
                divergence_pct = abs(onchain - cmc_price_usd) / cmc_price_usd * 100.0
                if divergence_pct > self._cfg.oracle_divergence_max_pct:
                    return ValidationResult(False, symbol, token, oracle_divergence_pct=divergence_pct,
                                            reason=RejectReason.ORACLE_DESYNC,
                                            detail=f"Divergence {divergence_pct:.2f}% (on-chain {onchain:.6f} vs CMC {cmc_price_usd:.6f}).")

            min_out = int(expected_out * (1 - self._cfg.max_slippage_pct / 100.0))
            return ValidationResult(True, symbol, token,
                                    estimated_slippage_pct=price_impact_pct,
                                    oracle_divergence_pct=divergence_pct,
                                    expected_out=expected_out, min_out=min_out,
                                    detail="Approved by Filter 2.")

        except Exception as exc:  # noqa: BLE001 — nonexistent pool / pair without liquidity revert here
            return ValidationResult(False, symbol, token, reason=RejectReason.NO_LIQUIDITY,
                                    detail=f"On-chain error (nonexistent pool or no liquidity): {exc}")

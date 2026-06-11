"""Filtro 2 — Validação física on-chain na BNB Smart Chain.

Recebe um sinal aprovado pelo Filtro 1 e, ANTES de qualquer assinatura, valida:
  1. Whitelist  — token pertence aos 149 elegíveis? (senão não conta no PnL)
  2. Liquidez/Slippage — simula a compra na PancakeSwap (getAmountsOut, só leitura)
  3. Taxa oculta — heurística round-trip detecta token com fee-on-transfer/burn
  4. Dessincronização de oráculo — preço on-chain vs preço da CMC

Tudo via chamadas `view` (eth_call) → custo ZERO de gás. Falha fecha o trade.

Limites conhecidos (v1): a checagem round-trip pega fee-on-transfer/tax via
matemática de reservas; um honeypot que REVERTE na venda real precisa de
simulação de swap com state-override (endurecimento futuro). Como o foco é em
tokens blue-chip líquidos, o risco residual é baixo.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from web3 import Web3

from boomerang.config import Config
from boomerang.types import RejectReason, ValidationResult

ROOT = Path(__file__).resolve().parent.parent.parent

# ABIs mínimas (só o necessário) ──────────────────────────────────────────────
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
# PancakeSwap V2 Factory — getPair p/ medir a profundidade (liquidez) do pool token/WBNB.
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
# PancakeSwap V3 — QuoterV2 (preço/liquidez na V3, que o roteador V2 não enxerga).
_V3_QUOTER = "0xB048Bbc1Ee6b733FFfCFb9e9CeF7375518e25997"
_V3_FEE_TIERS = (500, 2500, 100, 10000)  # ordem por probabilidade de ter pool
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
# Round-trip (comprar+vender) deve reter >= isto; abaixo = ilíquido/honeypot/taxa.
_MIN_ROUNDTRIP = 0.97

# Fee da PancakeSwap V2 por hop = 0,25% → fator de retenção por hop.
_PCS_FEE_PER_HOP = 0.0025

# WBNB (wrapped BNB) — usado só para precificar o saldo NATIVO de gás (BNB) em USDT.
_WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
# Saldos abaixo deste valor em USD são ignorados (poeira/arredondamento).
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
        self._executor = None  # cotador de agregador (TWAK); injetado via set_quoter()
        self._usdt = Web3.to_checksum_address(config.network["usdt_bsc_address"])
        self._wbnb = Web3.to_checksum_address(_WBNB)
        try:
            self._usdc = Web3.to_checksum_address(config.network["usdc_bsc_address"])
        except (KeyError, TypeError):
            self._usdc = None
        self._decimals_cache: dict[str, int] = {}
        self._whitelist = self._load_whitelist()
        self._token_map = self._load_token_map()  # symbol -> endereço (só tokens, sem base)

    # ── conectividade ────────────────────────────────────────────────────────
    def is_connected(self) -> bool:
        try:
            return self.w3.is_connected() and self.w3.eth.chain_id == int(self._cfg.network["bsc_chain_id"])
        except Exception as exc:  # noqa: BLE001
            self._log.error("Falha de conexão RPC: %s", exc)
            return False

    # ── whitelist ────────────────────────────────────────────────────────────
    def _load_whitelist(self) -> set[str]:
        path = ROOT / self._cfg.hackathon["eligible_tokens_file"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._log.warning("eligible_tokens.json não encontrado — whitelist vazia (fecha tudo).")
            return set()
        addrs = {Web3.to_checksum_address(a) for a in data.get("tokens", {}).values()}
        if not addrs:
            self._log.warning("Whitelist VAZIA — popular os 149 tokens na Fase 0. Trades bloqueados.")
        return addrs

    def _load_token_map(self) -> dict[str, str]:
        """symbol -> endereço (checksum) dos tokens-foco (sem a base stable)."""
        path = ROOT / self._cfg.hackathon["eligible_tokens_file"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        out: dict[str, str] = {}
        for sym, addr in data.get("tokens", {}).items():
            try:
                out[sym] = Web3.to_checksum_address(addr)
            except Exception:  # noqa: BLE001
                continue
        return out

    def is_whitelisted(self, token_address: str) -> bool:
        return Web3.to_checksum_address(token_address) in self._whitelist

    # ── helpers on-chain ─────────────────────────────────────────────────────
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
        """Injeta o executor TWAK para o Filtro 2 validar pela rota REAL (agregador)."""
        self._executor = executor

    # ── cotação V3 (single-hop, melhor fee tier) ─────────────────────────────
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
            except Exception:  # noqa: BLE001 — sem pool nesse tier reverte
                continue
        return best

    def _route_out(self, token_in: str, token_out: str, amount_in: int) -> int:
        """Melhor saída entre V2 (direto/via WBNB) e V3 (direto/via WBNB).

        Para na 1ª rota com liquidez (sondagem é pequena → preço ~igual em qualquer
        pool). Cobre tokens cuja liquidez migrou para a V3 que a V2 não enxerga.
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

    # ── preço spot on-chain (USD por 1 token) — agora cobre V2 E V3 ──────────
    def onchain_price_usd(self, token_address: str) -> float:
        """Preço de 1 token em USD, sondando com ~1 USDT (impacto ~0), via V2 ou V3.

        Sonda 'quantos tokens por 1 USDT' e inverte → robusto para tokens caros e
        baratos. Levanta ValueError se nenhuma rota tiver liquidez."""
        token = Web3.to_checksum_address(token_address)
        usdt_dec = self._decimals(self._usdt)
        probe = 10 ** usdt_dec  # ~1 USDT
        tokens_raw = self._route_out(self._usdt, token, probe)
        if tokens_raw <= 0:
            raise ValueError(f"Sem rota de preço (V2/V3) para {token}.")
        tokens = tokens_raw / (10 ** self._decimals(token))
        price = 1.0 / tokens if tokens > 0 else 0.0
        # Sanidade: a sonda direta V2/V3 dá preço-lixo em token de liquidez fina
        # (ex.: ATOM saiu $5927; em alguns nós, milhões), inflando equity e risco.
        # Nenhum token elegível passa de ~$2k (ETH). Acima de $5k = sonda quebrada:
        # trata como SEM preço (o wallet_breakdown ignora; não entra na equity).
        if price <= 0 or price > 5000:
            raise ValueError(f"Preço on-chain implausível para {token}: ${price:.2f}")
        return price

    def wbnb_pool_liquidity_usd(self, token_address: str) -> float:
        """SKILL Tamanho por liquidez: profundidade do pool token/WBNB (V2) em USD.

        Lê as reservas do par na PancakeSwap V2 e converte o lado WBNB em USD. É a
        medida direta de quão fundo/seguro é o mercado on-chain do token. Retorna 0.0
        se não há par (ou em erro) — o chamador trata como ilíquido."""
        try:
            token = Web3.to_checksum_address(token_address)
            pair = self._factory.functions.getPair(token, self._wbnb).call()
            if int(pair, 16) == 0:
                return 0.0
            c = self.w3.eth.contract(address=Web3.to_checksum_address(pair), abi=_PAIR_ABI)
            r0, r1, _ = c.functions.getReserves().call()
            token0 = Web3.to_checksum_address(c.functions.token0().call())
            wbnb_reserve = (r0 if token0 == self._wbnb else r1) / 1e18
            bnb_price = self._price_via([self._wbnb, self._usdt])  # USDT por 1 BNB
            return wbnb_reserve * bnb_price
        except Exception as exc:  # noqa: BLE001
            self._log.debug("Liquidez do pool indisponível p/ %s: %s", token_address, exc)
            return 0.0

    def _token_balance(self, token_address: str, holder: str) -> int:
        erc20 = self.w3.eth.contract(address=Web3.to_checksum_address(token_address), abi=_ERC20_ABI)
        return int(erc20.functions.balanceOf(Web3.to_checksum_address(holder)).call())

    def _price_via(self, path: list[str]) -> float:
        """Preço de 1 unidade do 1º token do path em USDT (cotação de tamanho 1)."""
        first = Web3.to_checksum_address(path[0])
        one = 10 ** self._decimals(first)
        out = self._amounts_out(one, path)
        return out[-1] / (10 ** self._decimals(self._usdt))

    # ── composição da carteira (saldos reais on-chain → USD) ──────────────────
    def wallet_breakdown(self, address: str) -> dict:
        """Lê os saldos REAIS da carteira on-chain e converte cada moeda em USD.

        Só leitura (get_balance / balanceOf via eth_call) — custo zero de gás e
        NÃO toca a chave privada. Retorna:
            {address, total_usd, holdings:[{symbol, kind, balance, price_usd, value_usd, pct}]}
        ordenado por valor decrescente, ignorando poeira (< $0.01).
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

        # 1) BNB nativo (reserva de gás)
        try:
            bnb = self.w3.eth.get_balance(holder) / 1e18
            if bnb > 0:
                add("BNB", "gas", bnb, self._price_via([self._wbnb, self._usdt]))
        except Exception as exc:  # noqa: BLE001
            self._log.warning("Saldo BNB indisponível: %s", exc)

        # 2) Stables (base operacional) — preço fixo $1
        stables = {"USDT": self._usdt}
        if self._usdc:
            stables["USDC"] = self._usdc
        for sym, addr in stables.items():
            try:
                bal = self._token_balance(addr, holder) / (10 ** self._decimals(addr))
                add(sym, "stable", bal, 1.0)
            except Exception as exc:  # noqa: BLE001
                self._log.warning("Saldo %s indisponível: %s", sym, exc)

        # 3) Tokens-foco (whitelist) com saldo > 0
        for sym, addr in self._token_map.items():
            try:
                raw = self._token_balance(addr, holder)
                if raw <= 0:
                    continue
                bal = raw / (10 ** self._decimals(addr))
                add(sym, "token", bal, self.onchain_price_usd(addr))
            except Exception as exc:  # noqa: BLE001
                self._log.debug("Token %s sem cotação/saldo: %s", sym, exc)

        for h in holdings:
            h["pct"] = (h["value_usd"] / total * 100.0) if total > 0 else 0.0
        holdings.sort(key=lambda h: h["value_usd"], reverse=True)
        return {"address": holder, "total_usd": total, "holdings": holdings}

    # ── validação completa ───────────────────────────────────────────────────
    def validate(
        self,
        *,
        symbol: str,
        token_address: str,
        amount_usd: float,
        cmc_price_usd: float | None = None,
    ) -> ValidationResult:
        """Filtro 2. Whitelist (sempre) + checagem de liquidez/saída.

        Se houver cotador de agregador (TWAK) injetado, valida pela ROTA REAL que a
        execução usará (cobre V2+V3+outros DEX). Senão, cai na simulação V2 (testes).
        """
        token = Web3.to_checksum_address(token_address)
        if not self.is_whitelisted(token):
            return ValidationResult(False, symbol, token, reason=RejectReason.NOT_WHITELISTED,
                                    detail="Token fora dos 149 elegíveis.")
        if self._executor is not None:
            return self._validate_via_aggregator(
                symbol=symbol, token=token, amount_usd=amount_usd, cmc_price_usd=cmc_price_usd)
        return self._validate_v2(
            symbol=symbol, token_address=token, amount_usd=amount_usd, cmc_price_usd=cmc_price_usd)

    def _validate_via_aggregator(
        self, *, symbol: str, token: str, amount_usd: float,
        cmc_price_usd: float | None = None,
    ) -> ValidationResult:
        """Valida comprando e revendendo via cotação do TWAK (round-trip real)."""
        # SKILL Tamanho por liquidez: o pool é fundo o suficiente? Protege contra
        # mercados rasos/manipuláveis e contra um trade grande demais p/ o pool.
        liq = self.wbnb_pool_liquidity_usd(token)
        min_liq = self._cfg.min_pool_liquidity_usd
        if liq < min_liq:
            return ValidationResult(False, symbol, token, reason=RejectReason.NO_LIQUIDITY,
                                    detail=f"Pool raso (~${liq:,.0f} < mínimo ${min_liq:,.0f}).")
        cap = liq * self._cfg.max_pool_share_pct / 100.0
        if amount_usd > cap:
            return ValidationResult(False, symbol, token, reason=RejectReason.HIGH_SLIPPAGE,
                                    detail=f"Trade ${amount_usd:.2f} > {self._cfg.max_pool_share_pct:.0f}% "
                                           f"do pool (~${liq:,.0f}); reduza o tamanho.")
        base = self._cfg.dev_safety["base_stable_symbol"]  # ex.: USDC
        try:
            buy = self._executor.quote(base, token, amount_usd, self._cfg.max_slippage_pct)
            tokens_out = float(buy.get("out") or 0.0)
            if tokens_out <= 0:
                return ValidationResult(False, symbol, token, reason=RejectReason.NO_LIQUIDITY,
                                        detail="Agregador não encontrou rota de compra.")
            sell = self._executor.quote(token, base, amount_usd, self._cfg.max_slippage_pct)
            usdc_back = float(sell.get("out") or 0.0)
        except Exception as exc:  # noqa: BLE001
            return ValidationResult(False, symbol, token, reason=RejectReason.NO_LIQUIDITY,
                                    detail=f"Cotação do agregador falhou: {exc}")

        # Round-trip: comprou $X e revendeu — quanto voltou? (pega slippage+taxa+iliquidez)
        retention = usdc_back / amount_usd if amount_usd > 0 else 0.0
        if retention < _MIN_ROUNDTRIP:
            perda = (1 - retention) * 100.0
            return ValidationResult(False, symbol, token, reason=RejectReason.BURNING_TAX,
                                    estimated_slippage_pct=perda,
                                    detail=f"Round-trip retém só {retention*100:.2f}% "
                                           f"(perda {perda:.2f}%) → ilíquido/taxa.")

        # Oráculo: preço efetivo da rota (USD por token) vs preço CMC.
        divergence_pct = None
        route_price = amount_usd / tokens_out  # USDC gastos por token recebido
        if cmc_price_usd and cmc_price_usd > 0:
            divergence_pct = abs(route_price - cmc_price_usd) / cmc_price_usd * 100.0
            if divergence_pct > self._cfg.oracle_divergence_max_pct:
                return ValidationResult(False, symbol, token, oracle_divergence_pct=divergence_pct,
                                        reason=RejectReason.ORACLE_DESYNC,
                                        detail=f"Divergência {divergence_pct:.2f}% "
                                               f"(rota {route_price:.6f} vs CMC {cmc_price_usd:.6f}).")
        impact = buy.get("price_impact")
        slip = float(impact) if impact is not None else (1 - retention) * 100.0
        return ValidationResult(True, symbol, token, estimated_slippage_pct=slip,
                                oracle_divergence_pct=divergence_pct,
                                detail=f"Aprovado pelo Filtro 2 (agregador, round-trip {retention*100:.2f}%).")

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
                                    detail="Token fora dos 149 elegíveis.")

        usdt_dec = self._decimals(self._usdt)
        amount_in = int(amount_usd * (10 ** usdt_dec))

        try:
            # 2) Simulação de compra USDT -> token
            buy = self._amounts_out(amount_in, [self._usdt, token])
            expected_out = buy[-1]
            if expected_out <= 0:
                return ValidationResult(False, symbol, token, reason=RejectReason.NO_LIQUIDITY,
                                        detail="Saída zero na simulação.")

            # impacto de preço: taxa efetiva vs taxa de referência (1 USDT)
            ref_in = 10 ** usdt_dec
            ref_out = self._amounts_out(ref_in, [self._usdt, token])[-1]
            rate_ref = ref_out / ref_in            # tokens por unidade base de USDT (sem impacto)
            rate_eff = expected_out / amount_in     # tokens por unidade base (nossa ordem)
            price_impact_pct = max((rate_ref - rate_eff) / rate_ref * 100.0, 0.0)

            if price_impact_pct > self._cfg.max_slippage_pct:
                return ValidationResult(False, symbol, token, estimated_slippage_pct=price_impact_pct,
                                        reason=RejectReason.HIGH_SLIPPAGE,
                                        detail=f"Impacto {price_impact_pct:.3f}% > teto {self._cfg.max_slippage_pct}%.")

            # 3) Taxa oculta (round-trip): vende de volta e mede retenção
            sell_back = self._amounts_out(expected_out, [token, self._usdt])[-1]
            retention = sell_back / amount_in
            # esperado sem taxa: duas fees de pool + 2x o impacto de preço
            expected_retention = (1 - _PCS_FEE_PER_HOP) ** 2 * (1 - 2 * price_impact_pct / 100.0)
            # tolerância de 1% para ruído/arredondamento
            if retention < expected_retention - 0.01:
                perda = (1 - retention) * 100.0
                return ValidationResult(False, symbol, token, reason=RejectReason.BURNING_TAX,
                                        detail=f"Round-trip retém só {retention*100:.2f}% (perda {perda:.2f}%) → taxa oculta.")

            # 4) Dessincronização de oráculo (se a CMC forneceu preço)
            divergence_pct = None
            if cmc_price_usd and cmc_price_usd > 0:
                onchain = self.onchain_price_usd(token)
                divergence_pct = abs(onchain - cmc_price_usd) / cmc_price_usd * 100.0
                if divergence_pct > self._cfg.oracle_divergence_max_pct:
                    return ValidationResult(False, symbol, token, oracle_divergence_pct=divergence_pct,
                                            reason=RejectReason.ORACLE_DESYNC,
                                            detail=f"Divergência {divergence_pct:.2f}% (on-chain {onchain:.6f} vs CMC {cmc_price_usd:.6f}).")

            min_out = int(expected_out * (1 - self._cfg.max_slippage_pct / 100.0))
            return ValidationResult(True, symbol, token,
                                    estimated_slippage_pct=price_impact_pct,
                                    oracle_divergence_pct=divergence_pct,
                                    expected_out=expected_out, min_out=min_out,
                                    detail="Aprovado pelo Filtro 2.")

        except Exception as exc:  # noqa: BLE001 — pool inexistente / par sem liquidez revertem aqui
            return ValidationResult(False, symbol, token, reason=RejectReason.NO_LIQUIDITY,
                                    detail=f"Erro on-chain (pool inexistente ou sem liquidez): {exc}")

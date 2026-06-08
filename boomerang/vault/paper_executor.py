"""PaperExecutor — execução SIMULADA (modo paper), risco e custo de trade zero.

Duck-type do TwakExecutor: o agente usa exatamente a mesma interface. A diferença:
  - buy/sell/transfer são SIMULADOS num livro-caixa interno (sem tx on-chain).
  - portfolio_usd vem do livro-caixa + preços REAIS on-chain (via BNBValidator).
  - x402_request é DELEGADO ao executor real (pagar dados da CMC é barato e
    necessário para sinais reais). Assim: dados/decisão REAIS, execução SIMULADA.

Ideal para validar toda a lógica antes de arriscar a banca de trade. Só precisa
de um pouco de USDC na Base (dados), nada de USDT/BNB de trade.
"""
from __future__ import annotations

import logging
import time

from boomerang.config import Config
from boomerang.types import ExecutionResult


class PaperExecutor:
    def __init__(self, config: Config, validator, *, starting_cash_usd: float = 100.0,
                 real_executor=None, logger: logging.Logger | None = None) -> None:  # noqa: ANN001
        self._cfg = config
        self._log = logger or logging.getLogger("boomerang.vault.paper")
        self._validator = validator            # para preços reais on-chain
        self._real = real_executor             # para x402 (dados CMC) reais
        self._cash = float(starting_cash_usd)  # stable simulada (USDT)
        self._holdings: dict[str, float] = {}  # token_address -> qty
        self._tx = 0

    def _next_tx(self, tag: str) -> str:
        self._tx += 1
        return f"PAPER-{tag}-{self._tx}"

    def _price(self, token: str) -> float:
        return self._validator.onchain_price_usd(token)

    # ── interface compatível com TwakExecutor ────────────────────────────────
    def portfolio_usd(self, password: str | None = None) -> float:
        total = self._cash
        for token, qty in self._holdings.items():
            if qty > 0:
                try:
                    total += qty * self._price(token)
                except Exception:  # noqa: BLE001
                    pass
        return round(total, 6)

    def buy(self, *, to_token: str, amount_usd: float, password: str | None = None,
            slippage_pct: float | None = None) -> ExecutionResult:
        if amount_usd > self._cash:
            return ExecutionResult(False, to_token, error="Paper: stable insuficiente.")
        price = self._price(to_token)
        qty = amount_usd / price
        self._cash -= amount_usd
        self._holdings[to_token] = self._holdings.get(to_token, 0.0) + qty
        self._log.info("[PAPER] BUY %s: $%.2f @ %.6f -> %.6f", to_token, amount_usd, price, qty)
        return ExecutionResult(True, to_token, tx_hash=self._next_tx("BUY"),
                               entry_price=price, qty=qty)

    def sell_all(self, *, token: str, amount: float, password: str | None = None,
                 slippage_pct: float | None = None) -> ExecutionResult:
        price = self._price(token)
        qty = amount if amount else self._holdings.get(token, 0.0)
        proceeds = qty * price
        self._cash += proceeds
        self._holdings[token] = max(self._holdings.get(token, 0.0) - qty, 0.0)
        self._log.info("[PAPER] SELL %s: %.6f @ %.6f -> $%.2f", token, qty, price, proceeds)
        return ExecutionResult(True, token, tx_hash=self._next_tx("SELL"))

    def transfer_to_owner(self, *, to: str, amount: float, token: str,
                          password: str | None = None, max_usd: float | None = None) -> dict:
        sent = min(amount, self._cash)
        self._cash -= sent
        return {"txHash": self._next_tx("WD"), "to": to, "amount": sent, "paper": True}

    def register_competition(self, password: str | None = None) -> dict:
        return {"paper": True, "note": "registro simulado (modo paper)"}

    def competition_status(self, password: str | None = None) -> dict:
        return {"paper": True, "registered": False}

    # x402 (dados CMC): delega ao executor real se houver; senão, indisponível.
    def x402_request(self, url: str, **kwargs):  # noqa: ANN003, ANN201
        if self._real is None:
            raise RuntimeError("PaperExecutor sem executor real para x402 (dados CMC).")
        return self._real.x402_request(url, **kwargs)

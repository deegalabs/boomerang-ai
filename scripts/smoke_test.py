"""Smoke test da fundação — valida config + motor de risco SEM dependências externas.

Roda com: python scripts/smoke_test.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from boomerang.config import load_config
from boomerang.risk import RiskEngine
from boomerang.risk.risk_engine import ExitSignal
from boomerang.types import Position

OK = "OK"


def main() -> int:
    cfg = load_config()
    print(f"[config] min_confidence_score (modo {cfg.user['mode']}): {cfg.min_confidence_score}")
    print(f"[config] drawdown safety/DQ: {cfg.drawdown_safety_pct}% / {cfg.drawdown_dq_pct}%")
    print(f"[config] position size: {cfg.position_size_pct}%  | stop: {cfg.user_stop_loss_pct}%")
    assert cfg.min_confidence_score == 70, "modo conservador deve exigir score 70"

    re = RiskEngine(cfg, initial_equity_usd=100.0)

    # 1. Dimensionamento: 5% de 100 = 5.0
    size = re.position_size_usd(100.0, available_stable_usd=100.0)
    assert size == 5.0, size
    print(f"[sizing] 5% de $100 = ${size}  {OK}")

    # 2. Sizing limitado pelo stable disponível
    assert re.position_size_usd(100.0, available_stable_usd=3.0) == 3.0
    # abaixo do mínimo operacional → 0
    assert re.position_size_usd(100.0, available_stable_usd=0.5) == 0.0
    print(f"[sizing] limite por stable e piso mínimo {OK}")

    # 3. Permissão de abertura
    d = re.can_open_position(current_equity_usd=100.0, available_stable_usd=100.0,
                            open_positions=0, now_ts=10_000.0)
    assert d.allowed, d.detail
    print(f"[gate] abertura permitida {OK}")

    # cooldown bloqueia
    re.record_trade(now_ts=10_000.0)
    d2 = re.can_open_position(current_equity_usd=100.0, available_stable_usd=100.0,
                             open_positions=0, now_ts=10_100.0)
    assert not d2.allowed and d2.reason.value == "REJECTED_COOLDOWN", d2
    print(f"[gate] cooldown bloqueia novo trade ({d2.detail}) {OK}")

    # 4. Circuit breaker: peak=100, cai p/ 77 => drawdown 23% => dispara
    re2 = RiskEngine(cfg, initial_equity_usd=100.0)
    re2.update_equity(120.0)  # pico sobe
    assert abs(re2.current_drawdown_pct(120.0)) < 1e-9
    # de 120, cair p/ 92.4 = 23% de drawdown
    assert re2.circuit_breaker_tripped(92.4), re2.current_drawdown_pct(92.4)
    assert not re2.circuit_breaker_tripped(100.0)
    print(f"[breaker] dispara em {cfg.drawdown_safety_pct}% sobre o pico {OK}")

    # 5. Stop-loss por trade: stop = entrada * (1 - stop_loss_pct)
    stop_pct = cfg.user_stop_loss_pct
    expected_stop = 100.0 * (1 - stop_pct / 100.0)
    pos = Position(symbol="ETH", token_address="0xabc", entry_price=100.0,
                   amount_usd=5.0, qty=0.05, stop_loss_price=re.initial_stop_price(100.0))
    assert abs(pos.stop_loss_price - expected_stop) < 1e-9, pos.stop_loss_price
    assert re.evaluate_position(pos, 99.0) == ExitSignal.HOLD
    assert re.evaluate_position(pos, expected_stop - 0.1) == ExitSignal.SELL_STOP_LOSS
    print(f"[stop] vende em -{stop_pct}% (stop {expected_stop}) e segura acima disso {OK}")

    # 6. Trailing stop: sobe +5% => stop vai p/ break-even e acompanha o pico
    pos2 = Position(symbol="ETH", token_address="0xabc", entry_price=100.0,
                    amount_usd=5.0, qty=0.05, stop_loss_price=re.initial_stop_price(100.0))
    re.evaluate_position(pos2, 106.0)        # ativa trailing (>= +5%)
    assert pos2.trailing_active
    assert pos2.stop_loss_price >= 100.0      # break-even ou acima
    re.evaluate_position(pos2, 110.0)         # pico sobe → stop acompanha
    assert pos2.stop_loss_price >= 104.0      # 110 * (1 - 5%) = 104.5
    # se cair abaixo do stop acompanhado → saída por trailing (no lucro)
    assert re.evaluate_position(pos2, 104.0) == ExitSignal.SELL_TRAILING
    print(f"[trailing] +5% trava break-even, acompanha o pico e sai no lucro {OK}")

    # 7. Heartbeat
    assert not re.needs_heartbeat(now_ts=10_000.0 + 3600)   # 1h depois: ok
    assert re.needs_heartbeat(now_ts=10_000.0 + 21 * 3600)  # 21h depois: precisa
    print(f"[heartbeat] dispara após {cfg.heartbeat_after_hours}h sem operar {OK}")

    print("\n[PASS] Fundacao validada: config + motor de risco corretos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

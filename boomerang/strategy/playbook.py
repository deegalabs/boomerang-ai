"""Playbook MULTI-ESTRATÉGIA roteado pelo REGIME de mercado.

Três estratégias, cada uma com gatilho de entrada DETERMINÍSTICO e gestão de
saída própria. O seletor escolhe a estratégia ativa pelo regime (medo/ganância)
e pelos sinais do token; o cérebro (Opus) CONFIRMA o setup (go/no-go + convicção).
As travas globais (disjuntor de drawdown, cap diário, depeg, liquidez/oráculo)
continuam valendo POR CIMA de qualquer estratégia.

  1) MOMENTUM (tendência de alta): pega empuxo jovem com volume subindo; corta a
     perda curtíssima e deixa o vencedor correr (trailing).
  2) MEAN-REVERSION (lateral/chop): compra um DIP curto de um token forte no dia,
     apostando no retorno à média; TP e SL fixos e cirúrgicos.
  3) DCA (pânico/medo extremo): compra ativo sólido em queda livre visando repique;
     sem SL fixo (coberto pelo disjuntor global + time-stop de 24h).

NOTA (proxy de volume): a CMC no nosso plano não expõe volume HORÁRIO, então o
filtro "vol 1h >= 15% do 24h" da estratégia de momentum é aproximado por
volume_change_24h_pct (surto de interesse no dia). É uma proxy de "interesse
subindo", não o 1h exato.
"""
from __future__ import annotations

from dataclasses import dataclass

# ── Limiares dos gatilhos (proxies/ajustáveis) ───────────────────────────────
VOL_ACCEL_MIN = 20.0    # momentum: volume_change_24h_pct >= 20% = interesse subindo (proxy do 1h)
VOL_STABLE_MAX = 40.0   # mean-rev: volume não pode estar EXPLODINDO (range, sem tendência)
PANIC_FNG = 25          # F&G < 25 = medo extremo → regime de pânico (só DCA opera)


@dataclass(frozen=True)
class StrategySpec:
    """Definição de uma estratégia: identidade + parâmetros de SAÍDA.

    stop_pct=0 → SEM SL fixo (depende do disjuntor global). take_profit_pct=0 →
    sem teto fixo (deixa correr via trailing). trailing_trigger_pct=0 → sem trailing.
    time_stop_min=0 → usa o time-stop global do config. band 999 → time-stop por
    TEMPO puro (sai no prazo independente do PnL)."""

    key: str
    label: str
    stop_pct: float
    take_profit_pct: float
    trailing_trigger_pct: float
    trailing_pct: float
    time_stop_min: float
    time_stop_band_pct: float


MOMENTUM = StrategySpec(
    "momentum", "Momentum/Atenção",
    stop_pct=1.0, take_profit_pct=0.0,
    trailing_trigger_pct=2.5, trailing_pct=1.5,
    time_stop_min=20.0, time_stop_band_pct=0.2,
)

MEAN_REVERSION = StrategySpec(
    "mean_reversion", "Reversão à Média",
    stop_pct=0.8, take_profit_pct=1.2,
    trailing_trigger_pct=0.0, trailing_pct=0.0,
    time_stop_min=0.0, time_stop_band_pct=0.0,
)

DCA = StrategySpec(
    "dca", "DCA/Acumulação",
    stop_pct=0.0, take_profit_pct=3.0,                 # sem SL fixo; TP +3% no repique
    trailing_trigger_pct=0.0, trailing_pct=0.0,
    time_stop_min=1440.0, time_stop_band_pct=999.0,    # 24h, por TEMPO puro (band 999 = qualquer PnL)
)

ALL = (MOMENTUM, MEAN_REVERSION, DCA)
_BY_KEY = {s.key: s for s in ALL}


def by_key(key: str) -> StrategySpec | None:
    return _BY_KEY.get(key or "")


def select_strategy(fng: int | None, metrics: dict) -> StrategySpec | None:
    """Roteia para a estratégia ativa pelo REGIME (medo/ganância) + sinais do token.
    Retorna a StrategySpec cujo gatilho determinístico DISPARA, ou None.

    Os gatilhos são quase mutuamente exclusivos (1h não pode ser >+2,5% e <-2% ao
    mesmo tempo). Em PÂNICO, SÓ a DCA opera (scalping seria estopado pela volatilidade)."""
    p1 = metrics.get("percent_change_1h")
    p24 = metrics.get("percent_change_24h")
    if p1 is None or p24 is None:
        return None
    vc = metrics.get("volume_change_24h_pct") or 0.0

    # PÂNICO (medo extremo): proíbe scalping; só DCA em queda livre de ativo sólido.
    if fng is not None and fng < PANIC_FNG:
        if p24 < -10.0:
            return DCA
        return None

    # MOMENTUM: empuxo jovem (1h forte), alinhado em alta (24h>0), com volume subindo.
    if p1 > 2.5 and p24 > 0.0 and vc >= VOL_ACCEL_MIN:
        return MOMENTUM

    # MEAN-REVERSION: dip curto (1h<-2%) de um token FORTE no dia (24h>+4%), range
    # (volume estável, não explodindo = sem tendência).
    if p1 < -2.0 and p24 > 4.0 and vc <= VOL_STABLE_MAX:
        return MEAN_REVERSION

    return None


def setup_strength(spec: StrategySpec, metrics: dict) -> float:
    """Força do setup p/ RANQUEAR candidatos do ciclo (qual avaliar primeiro no Opus)."""
    p1 = metrics.get("percent_change_1h") or 0.0
    p24 = metrics.get("percent_change_24h") or 0.0
    vc = metrics.get("volume_change_24h_pct") or 0.0
    if spec.key == "momentum":
        return 50.0 + min(p1, 10.0) * 2.0 + min(max(vc, 0.0), 100.0) * 0.3
    if spec.key == "mean_reversion":
        return 40.0 + min(p24, 20.0) + min(-p1, 10.0)
    return 30.0 + min(-p24, 40.0)  # dca: quanto mais fundo a queda, maior o repique potencial

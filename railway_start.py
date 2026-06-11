"""Entrypoint único da Railway: roda o SITE público + o AGENTE no mesmo container.

Os dois compartilham o diretório de estado (volume), então o /live mostra os trades
reais do agente. O site roda na thread principal (responde /healthz na hora); o agente
roda numa thread própria com seu próprio event loop — assim chamadas bloqueantes do
twak não travam o site, e um crash do agente não derruba a web.

Antes de subir, materializa o keystore cifrado do TWAK e (só na 1a vez) o estado, a
partir de env vars base64. Assim nada sensível precisa ficar no repositório.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger("railway_start")


def _seed_file(env_key: str, dest: Path, *, only_if_absent: bool = False) -> None:
    b64 = os.getenv(env_key)
    if not b64:
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    if only_if_absent and dest.exists():
        return
    dest.write_bytes(base64.b64decode(b64))
    log.info("seed: %s -> %s", env_key, dest)


def _bootstrap() -> None:
    # Keystore cifrado do TWAK (mesma carteira de trade). Sempre materializa.
    _seed_file("TWAK_WALLET_JSON_B64", Path(os.path.expanduser("~/.twak/wallet.json")))
    # Estado inicial (posições/peak) só na 1a vez; depois o volume é a fonte de verdade.
    state_dir = Path(os.getenv("BOOMERANG_STATE_DIR", "state"))
    _seed_file("STATE_SEED_B64", state_dir / "agent_state.json", only_if_absent=True)


def _run_agent() -> None:
    """Roda o agente com SUPERVISÃO: se cair (exceção ou retorno inesperado), espera um
    backoff e REINICIA. Sem isso, um crash no startup deixava o agente morto até um
    re-deploy manual (com o site verde, sem ninguém saber). Encerra limpo só no shutdown."""
    import sys
    import time
    import traceback
    attempt = 0
    while True:
        attempt += 1
        os.environ["BOOMERANG_RESTART_COUNT"] = str(attempt - 1)  # 0 no 1º; >0 = reinício
        try:
            from run_agent import main as agent_main
            print(f">>> [agent-thread] iniciando (tentativa {attempt})", flush=True)
            asyncio.run(agent_main())
            print(">>> [agent-thread] main() retornou inesperadamente; reiniciando.", flush=True)
        except (KeyboardInterrupt, SystemExit):
            print(">>> [agent-thread] encerrando (shutdown).", flush=True)
            return
        except Exception as exc:  # noqa: BLE001
            print(f">>> [agent-thread] CAIU: {exc!r}; reiniciando.", flush=True)
            traceback.print_exc()
            sys.stdout.flush()
        time.sleep(min(60, 5 * attempt))  # backoff: 5s, 10s, ... até 60s


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    for noisy in ("httpx", "httpcore", "telegram", "telegram.ext", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # não vazar token na URL
    print(">>> [launcher] bootstrap (wallet/state)", flush=True)
    _bootstrap()
    print(">>> [launcher] iniciando thread do agente", flush=True)
    # Agente numa thread isolada (event loop próprio).
    threading.Thread(target=_run_agent, name="boomerang-agent", daemon=True).start()
    print(">>> [launcher] subindo site (uvicorn)", flush=True)
    # Site público na thread principal — responde /healthz imediatamente.
    import uvicorn
    from boomerang.webapp.site import app
    port = int(os.getenv("PORT", "8080"))
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info"))
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())

"""Single Railway entrypoint: runs the public SITE + the AGENT in the same container.

Both share the state directory (volume), so /live shows the agent's real trades. The
site runs on the main thread (answers /healthz instantly); the agent runs on its own
thread with its own event loop — that way blocking twak calls don't freeze the site,
and an agent crash doesn't take down the web.

Before starting, it materializes TWAK's encrypted keystore and (only the 1st time) the
state, from base64 env vars. That way nothing sensitive needs to live in the repository.
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
    # TWAK's encrypted keystore (same trading wallet). Always materialized.
    _seed_file("TWAK_WALLET_JSON_B64", Path(os.path.expanduser("~/.twak/wallet.json")))
    # Initial state (positions/peak) only the 1st time; afterwards the volume is the source of truth.
    state_dir = Path(os.getenv("BOOMERANG_STATE_DIR", "state"))
    _seed_file("STATE_SEED_B64", state_dir / "agent_state.json", only_if_absent=True)


def _run_agent() -> None:
    """Runs the agent with SUPERVISION: if it goes down (exception or unexpected return), it
    waits for a backoff and RESTARTS. Without this, a startup crash left the agent dead until a
    manual re-deploy (with the site green, nobody knowing). Shuts down cleanly only on shutdown."""
    import sys
    import time
    import traceback
    attempt = 0
    while True:
        attempt += 1
        os.environ["BOOMERANG_RESTART_COUNT"] = str(attempt - 1)  # 0 on the 1st; >0 = restart
        try:
            from run_agent import main as agent_main
            print(f">>> [agent-thread] starting (attempt {attempt})", flush=True)
            asyncio.run(agent_main())
            print(">>> [agent-thread] main() returned unexpectedly; restarting.", flush=True)
        except (KeyboardInterrupt, SystemExit):
            print(">>> [agent-thread] terminating (shutdown).", flush=True)
            return
        except Exception as exc:  # noqa: BLE001
            print(f">>> [agent-thread] CRASHED: {exc!r}; restarting.", flush=True)
            traceback.print_exc()
            sys.stdout.flush()
        time.sleep(min(60, 5 * attempt))  # backoff: 5s, 10s, ... up to 60s


async def main() -> None:
    logging.basicConfig(level=logging.INFO)
    for noisy in ("httpx", "httpcore", "telegram", "telegram.ext", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)  # don't leak the token in the URL
    print(">>> [launcher] bootstrap (wallet/state)", flush=True)
    _bootstrap()
    print(">>> [launcher] starting agent thread", flush=True)
    # Agent on an isolated thread (its own event loop).
    threading.Thread(target=_run_agent, name="boomerang-agent", daemon=True).start()
    print(">>> [launcher] bringing up site (uvicorn)", flush=True)
    # Public site on the main thread — answers /healthz immediately.
    import uvicorn
    from boomerang.webapp.site import app
    port = int(os.getenv("PORT", "8080"))
    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=port, log_level="info"))
    await server.serve()


if __name__ == "__main__":
    asyncio.run(main())

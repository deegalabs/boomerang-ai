"""One-off: migra os segredos do .env + keystore/estado para as variáveis da Railway.

Nunca imprime VALORES — só os nomes das chaves setadas. Os blobs sensíveis
(keystore cifrado, estado) vão como base64. Rode uma vez; pode apagar depois.
"""
import base64
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent.parent
SERVICE = "boomerang-ai"

env = dotenv_values(ROOT / ".env")


def b64(path: Path) -> str | None:
    try:
        return base64.b64encode(path.read_bytes()).decode()
    except FileNotFoundError:
        return None


# Segredos/él config que o AGENTE precisa na nuvem (o site já tem os dele).
KEYS = ["ANTHROPIC_API_KEY", "CMC_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_MASTER_USER_ID",
        "TWAK_ACCESS_ID", "TWAK_HMAC_SECRET", "WALLET_PASSWORD", "LLM_MODEL", "BSC_RPC_URL"]
vars_to_set: dict[str, str] = {k: env[k] for k in KEYS if env.get(k)}

# twak usa TWAK_WALLET_PASSWORD como fallback (sem keychain no Linux).
if env.get("WALLET_PASSWORD"):
    vars_to_set["TWAK_WALLET_PASSWORD"] = env["WALLET_PASSWORD"]

# Caminho do binário do twak dentro do container + diretório de estado no volume.
vars_to_set["TWAK_BIN"] = "/app/node_modules/.bin/twak"
vars_to_set["BOOMERANG_STATE_DIR"] = "/app/state"

# Keystore cifrado do TWAK (mesma carteira) e estado inicial (posições) — base64.
wallet_b64 = b64(Path(os.path.expanduser("~/.twak/wallet.json")))
if wallet_b64:
    vars_to_set["TWAK_WALLET_JSON_B64"] = wallet_b64
state_b64 = b64(ROOT / "state" / "agent_state.json")
if state_b64:
    vars_to_set["STATE_SEED_B64"] = state_b64

# Monta um único `railway variables --set K=V ...` (valores não são impressos por nós).
args = (["cmd", "/c"] if os.name == "nt" else []) + ["railway", "variables", "--service", SERVICE]
for k, v in vars_to_set.items():
    args += ["--set", f"{k}={v}"]

print(f"Setando {len(vars_to_set)} variáveis na Railway: {sorted(vars_to_set)}")
proc = subprocess.run(args, capture_output=True, text=True)
if proc.returncode != 0:
    print("ERRO:", (proc.stderr or proc.stdout)[:500])
    sys.exit(1)
print("OK — variáveis setadas (valores não exibidos).")

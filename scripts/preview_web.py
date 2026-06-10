"""Preview LOCAL da web do Boomerang AI — porta 8090.

Runner fino: o app de verdade mora em boomerang/webapp/site.py (mesmo app do deploy).
Use isto para desenvolver/ver o site localmente sem tocar no agente (porta 8080).

Uso: .venv\\Scripts\\python scripts\\preview_web.py   → http://localhost:8090
Produção: uvicorn boomerang.webapp.site:app --host 0.0.0.0 --port 8090
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn
from dotenv import load_dotenv

from boomerang.webapp.site import app

load_dotenv()

if __name__ == "__main__":
    print("Preview da web em  ->  http://localhost:8090   (Ctrl+C para parar)")
    uvicorn.run(app, host="127.0.0.1", port=8090, log_level="warning")

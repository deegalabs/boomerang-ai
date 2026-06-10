# Boomerang AI — imagem única (site + agente) para a Railway.
# Python (app) + Node 20 (TWAK CLI, autocustódia + x402).
FROM python:3.12-slim

# Node 20 + toolchain de build (algumas wheels precisam de gcc).
RUN apt-get update && apt-get install -y --no-install-recommends \
      curl ca-certificates gnupg build-essential \
 && curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
 && apt-get install -y --no-install-recommends nodejs \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependências Python (cache: copia só o requirements primeiro).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# TWAK CLI (Trust Wallet Agent Kit) — execução self-custody + x402.
RUN npm install @trustwallet/cli
ENV TWAK_BIN=/app/node_modules/.bin/twak

# Código da aplicação.
COPY . .

ENV PYTHONUNBUFFERED=1
ENV HOME=/root

# Sobe site + agente (ver railway_start.py).
CMD ["python", "railway_start.py"]

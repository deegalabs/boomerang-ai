"""Proxy x402 deployável: injeta o header Accept que o MCP da CMC exige.

Por que existe: o cliente x402 do twak não envia `Accept: application/json,
text/event-stream` (o MCP da CMC exige) e não aceita header customizado -> 400; e
o twak recusa endereços privados/loopback, exigindo um endpoint PÚBLICO. Na VPS,
este proxy roda em loopback ATRÁS de um reverse proxy (Caddy/nginx) que termina o
TLS num domínio público. O twak chama o domínio público; o reverse proxy repassa
pra cá; aqui injetamos o header e encaminhamos pra CMC (com a assinatura de
pagamento). Assim a carteira de trade paga x402 sem expor chave nem precisar de VPS
dedicada só pra isso.

Config por ambiente:
  X402_PROXY_HOST   (padrão 127.0.0.1)   bind do proxy (loopback atrás do reverse proxy)
  X402_PROXY_PORT   (padrão 8402)
  X402_TARGET       (padrão MCP x402 da CMC)
  X402_PROXY_CERT / X402_PROXY_KEY  (opcional) habilita HTTPS direto (uso standalone)

Uso na VPS:   python -m boomerang.webapp.x402_proxy
"""
from __future__ import annotations

import http.server
import os
import socketserver
import ssl
import sys
import urllib.error
import urllib.request

TARGET = os.getenv("X402_TARGET", "https://mcp.coinmarketcap.com/x402/mcp")
HOST = os.getenv("X402_PROXY_HOST", "127.0.0.1")
PORT = int(os.getenv("X402_PROXY_PORT", "8402"))
# headers do twak que precisam chegar à CMC (assinatura de pagamento etc.)
FORWARD = ("payment-signature", "x-payment", "x-payment-signature", "mcp-protocol-version")


class Handler(http.server.BaseHTTPRequestHandler):
    def _relay(self):
        n = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(n) if n else None
        fwd = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
        for k in self.headers:
            if k.lower() in FORWARD:
                fwd[k] = self.headers[k]
        try:
            r = urllib.request.urlopen(
                urllib.request.Request(TARGET, data=body, headers=fwd, method=self.command),
                timeout=40)
            status, rheaders, data = r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            status, rheaders, data = e.code, e.headers, e.read()
        except Exception as exc:  # noqa: BLE001
            status, rheaders, data = 502, {}, str(exc).encode()
        self.send_response(status)
        for k in rheaders:
            if k.lower() in ("payment-required", "content-type"):
                self.send_header(k, rheaders[k])
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    do_GET = _relay
    do_POST = _relay

    def log_message(self, fmt, *args):  # log enxuto
        sys.stdout.write("[x402-proxy] " + (fmt % args) + "\n")
        sys.stdout.flush()


def main() -> None:
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    with socketserver.ThreadingTCPServer((HOST, PORT), Handler) as srv:
        cert, key = os.getenv("X402_PROXY_CERT"), os.getenv("X402_PROXY_KEY")
        scheme = "http"
        if cert and key:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(cert, key)
            srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
            scheme = "https"
        print(f"x402 proxy ({scheme}) em {scheme}://{HOST}:{PORT}/  ->  {TARGET}", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()

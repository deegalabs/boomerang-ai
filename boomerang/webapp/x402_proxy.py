"""Deployable x402 proxy: injects the Accept header that the CMC MCP requires.

Why it exists: twak's x402 client does not send `Accept: application/json,
text/event-stream` (which the CMC MCP requires) and does not accept a custom header -> 400; and
twak refuses private/loopback addresses, requiring a PUBLIC endpoint. On the VPS,
this proxy runs on loopback BEHIND a reverse proxy (Caddy/nginx) that terminates
TLS on a public domain. twak calls the public domain; the reverse proxy forwards
here; here we inject the header and forward to CMC (with the payment
signature). This way the trading wallet pays x402 without exposing a key or needing a VPS
dedicated just for this.

Per-environment config:
  X402_PROXY_HOST   (default 127.0.0.1)   proxy bind (loopback behind the reverse proxy)
  X402_PROXY_PORT   (default 8402)
  X402_TARGET       (default CMC x402 MCP)
  X402_PROXY_CERT / X402_PROXY_KEY  (optional) enables direct HTTPS (standalone use)

Usage on the VPS:   python -m boomerang.webapp.x402_proxy
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
# twak headers that need to reach CMC (payment signature, etc.)
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

    def log_message(self, fmt, *args):  # lean log
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
        print(f"x402 proxy ({scheme}) on {scheme}://{HOST}:{PORT}/  ->  {TARGET}", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            sys.exit(0)


if __name__ == "__main__":
    main()

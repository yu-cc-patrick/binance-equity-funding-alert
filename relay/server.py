#!/usr/bin/env python3
"""极简中转: 把 /fapi/* 请求转发到 fapi.binance.com。

部署在非美区(如 Render 新加坡区), 用来绕过币安对美区 IP 的 451 封锁。
只转发币安合约的公开只读接口, 可选用 RELAY_TOKEN 做简单鉴权。
仅用 Python 标准库, 无第三方依赖。
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

UPSTREAM = "https://fapi.binance.com"
RELAY_TOKEN = os.environ.get("RELAY_TOKEN", "")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # 健康检查(Render 用)
        if self.path in ("/", "/health"):
            self._send(200, b'{"ok":true}')
            return
        # 可选鉴权
        if RELAY_TOKEN and self.headers.get("X-Relay-Token") != RELAY_TOKEN:
            self._send(403, b'{"error":"forbidden"}')
            return
        # 只允许转发币安合约只读接口
        if not self.path.startswith("/fapi/"):
            self._send(404, b'{"error":"only /fapi/ is allowed"}')
            return
        try:
            req = urllib.request.Request(
                UPSTREAM + self.path,
                headers={"User-Agent": "relay/1.0"},
            )
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            self._send(200, data)
        except Exception as exc:  # noqa: BLE001
            self._send(502, json.dumps({"error": str(exc)}).encode())

    def log_message(self, *args):  # 静音访问日志
        pass

    def _send(self, code, body):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "10000"))
    print(f"relay listening on 0.0.0.0:{port} -> {UPSTREAM}")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

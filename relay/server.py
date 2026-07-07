#!/usr/bin/env python3
"""极简中转: 转发到币安 + 推送到 PushPlus/微信。

部署在非美区(如 Render 新加坡区), 用来绕过币安对美区 IP 的 451 封锁,
同时也帮 GHA 美区 runner 中转 PushPlus 请求(中国服务,美区可能连不上)。
只转发公开只读接口 + PushPlus, 可选用 RELAY_TOKEN 做简单鉴权。
仅用 Python 标准库, 无第三方依赖。
"""
import json
import os
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

UPSTREAM_BINANCE = "https://fapi.binance.com"
UPSTREAM_WEB = "https://www.binance.com"         # 公告/网页 API
UPSTREAM_PUSHPLUS = "https://www.pushplus.plus/send"
RELAY_TOKEN = os.environ.get("RELAY_TOKEN", "")

COMMON_HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; funding-monitor/1.0)",
                  "Accept": "application/json"}


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
        # 币安合约接口
        if self.path.startswith("/fapi/"):
            upstream = UPSTREAM_BINANCE
        # 币安网页/公告 API
        elif self.path.startswith("/bapi/"):
            upstream = UPSTREAM_WEB
        else:
            self._send(404, b'{"error":"only /fapi/ and /bapi/ are allowed"}')
            return
        try:
            req = urllib.request.Request(upstream + self.path, headers=COMMON_HEADERS)
            with urllib.request.urlopen(req, timeout=20) as r:
                data = r.read()
            self._send(200, data)
        except Exception as exc:  # noqa: BLE001
            self._send(502, json.dumps({"error": str(exc)}).encode())

    def do_POST(self):
        # PushPlus 中转: GHA 美区 runner 可能连不上 pushplus.plus,
        # 经由新加坡 relay 代发到微信
        if self.path == "/pushplus":
            if RELAY_TOKEN and self.headers.get("X-Relay-Token") != RELAY_TOKEN:
                self._send(403, b'{"error":"forbidden"}')
                return
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b"{}"
            try:
                req = urllib.request.Request(
                    UPSTREAM_PUSHPLUS, data=body,
                    headers={"Content-Type": "application/json",
                             "User-Agent": "relay/1.0"},
                )
                with urllib.request.urlopen(req, timeout=20) as r:
                    data = r.read()
                self._send(200, data)
            except Exception as exc:  # noqa: BLE001
                self._send(502, json.dumps({"error": str(exc)}).encode())
            return
        self._send(404, b'{"error":"not found"}')

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
    print(f"relay listening on 0.0.0.0:{port} -> binance + pushplus")
    HTTPServer(("0.0.0.0", port), Handler).serve_forever()

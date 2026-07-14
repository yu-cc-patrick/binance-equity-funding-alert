#!/usr/bin/env python3
"""庄股扫描器: 涨幅 Top20 + 资金费率>0.01% + 24h OI 增>20% + 价格破 14 日低 → 预警。

独立脚本, 不动 monitor.py / check_delisting.py。每币每天最多报一次。
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SCANNER_STATE = os.path.join(SCRIPT_DIR, "scanner_state.json")

# —— 阈值 ——
FR_THRESHOLD = 0.0001         # 资金费率 > 0.01% (正费率)
OI_INCREASE_PCT = 20          # 24h OI 增幅 > 20%
KLINES_DAYS = 15              # 拿最近 15 根日 K(第 14 天前的 low)

# —— 密钥(复用 Keychain / env) ——
KEYCHAIN_PREFIX = "binance-alert/"


def _load_dotenv():
    p = os.path.join(SCRIPT_DIR, ".env")
    if not os.path.exists(p):
        return
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _keychain_get(name):
    if not sys.platform.startswith("darwin"):
        return ""
    try:
        r = subprocess.run(["security", "find-generic-password",
                            "-s", KEYCHAIN_PREFIX + name, "-w"],
                           capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def get_secret(name, default=""):
    v = os.environ.get(name)
    if v:
        return v
    v = _keychain_get(name)
    return v if v else default


_load_dotenv()
BINANCE_BASE = get_secret("BINANCE_BASE", "https://fapi.binance.com").rstrip("/")
RELAY_TOKEN = get_secret("RELAY_TOKEN")
TG_TOKEN = get_secret("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = get_secret("TELEGRAM_CHAT_ID")
PP_TOKEN = get_secret("PUSHPLUS_TOKEN")


def http_get_json(url):
    """GET + JSON + 3 次重试(复用 relay 鉴权)。"""
    headers = {"User-Agent": "scanner/1.0"}
    if RELAY_TOKEN:
        headers["X-Relay-Token"] = RELAY_TOKEN
    last_err = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            if attempt < 3:
                time.sleep(3 * attempt)
    raise last_err


# —— 推送 ——

def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    data = json.dumps({
        "chat_id": TG_CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15):
        pass


def send_wechat(text):
    if not PP_TOKEN:
        return
    title = text.split("\n")[0][:100]
    body = json.dumps({"token": PP_TOKEN, "title": title, "content": text, "template": "html"}).encode("utf-8")
    url = BINANCE_BASE + "/pushplus"
    headers = {"Content-Type": "application/json"}
    if RELAY_TOKEN:
        headers["X-Relay-Token"] = RELAY_TOKEN
    try:
        req = urllib.request.Request(url, data=body, headers=headers)
        with urllib.request.urlopen(req, timeout=20):
            pass
    except Exception as e:
        print(f"[WARN] 微信发送失败: {e}")


def broadcast(text):
    send_telegram(text)
    send_wechat(text)


# —— 数据获取 ——

def top20_gainers():
    """拉取 24hr ticker, 按涨幅降序取前 20 个 USDT 永续。"""
    tickers = http_get_json(f"{BINANCE_BASE}/fapi/v1/ticker/24hr")
    usdt = [t for t in tickers if t.get("symbol", "").endswith("USDT")]
    usdt.sort(key=lambda t: float(t.get("priceChangePercent", 0) or 0), reverse=True)
    return usdt[:20]


def funding_rate(symbol):
    """获取最新资金费率。"""
    data = http_get_json(f"{BINANCE_BASE}/fapi/v1/fundingRate?symbol={symbol}&limit=1")
    if isinstance(data, list) and data:
        return float(data[0]["fundingRate"])
    return 0.0


def oi_24h_change(symbol):
    """计算最近 24 小时 OI 变化百分比。"""
    oi_list = http_get_json(
        f"{BINANCE_BASE}/fapi/v1/openInterestHist?symbol={symbol}&period=1h&limit=25"
    )
    if not isinstance(oi_list, list) or len(oi_list) < 2:
        return 0.0
    # 取最早的一条(约 24h 前)和最新的一条
    oldest = float(oi_list[0]["sumOpenInterestValue"])
    newest = float(oi_list[-1]["sumOpenInterestValue"])
    if oldest == 0:
        return 0.0
    return (newest - oldest) / oldest * 100


def price_14d_low(symbol):
    """获取 14 天前(不含今天)的最低价。"""
    klines = http_get_json(
        f"{BINANCE_BASE}/fapi/v1/klines?symbol={symbol}&interval=1d&limit={KLINES_DAYS}"
    )
    if not isinstance(klines, list) or len(klines) < 2:
        return None
    # 排除最后一根(可能是今天未走完的 K), 取前 14 根的最低价
    historical = klines[:-1]
    lows = [float(k[3]) for k in historical]  # index 3 = low
    return min(lows)


def current_price(symbol):
    """获取当前最新成交价。"""
    ticker = http_get_json(f"{BINANCE_BASE}/fapi/v1/ticker/price?symbol={symbol}")
    return float(ticker["price"])


# —— 状态去重 ——

TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def load_alerted():
    """返回 {symbol_date: True} 的集合, 用于一天只报一次。"""
    if not os.path.exists(SCANNER_STATE):
        return {}
    try:
        with open(SCANNER_STATE, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_alerted(state):
    with open(SCANNER_STATE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


# —— 主逻辑 ——

def main():
    start = time.monotonic()
    candidates = top20_gainers()
    if not candidates:
        print("[ERROR] 未获取到 ticker 数据", file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] 涨幅 Top20: {len(candidates)} 个")

    alerted = load_alerted()
    # 清理过期键(不是今天的)
    alerted = {k: v for k, v in alerted.items() if k.endswith("_" + TODAY)}
    alerts = []

    for i, t in enumerate(candidates):
        sym = t["symbol"]
        pct_change = float(t.get("priceChangePercent", 0) or 0)
        day_key = f"{sym}_{TODAY}"

        if alerted.get(day_key):
            continue  # 今天已报过

        # 1) 资金费率
        try:
            fr = funding_rate(sym)
        except Exception as e:
            print(f"  [{i+1}/20] {sym} FR 失败: {e}")
            continue
        if fr <= FR_THRESHOLD:
            continue

        # 2) OI 24h 变化
        try:
            oi_chg = oi_24h_change(sym)
        except Exception as e:
            print(f"  [{i+1}/20] {sym} OI 失败: {e}")
            continue
        if oi_chg <= OI_INCREASE_PCT:
            continue

        # 3) 价格是否破 14 日低
        try:
            low14 = price_14d_low(sym)
            price = current_price(sym)
        except Exception as e:
            print(f"  [{i+1}/20] {sym} K线/价格 失败: {e}")
            continue
        if low14 is None or price >= low14:
            continue

        # 全部满足 → 告警！
        msg = (
            f"🔍 <b>[庄股扫描] {sym}</b>\n"
            f"涨幅: {pct_change:+.2f}% | FR: {fr*100:+.4f}% | OI 24h: +{oi_chg:.1f}%\n"
            f"当前价 {price} &lt; 14日前最低 {low14}\n"
            f"<i>去检查 CVD 等</i>"
        )
        alerts.append(msg)
        alerted[day_key] = True
        print(f"  [{i+1}/20] ✅ {sym} 命中! 涨幅{pct_change:+.1f}% FR{fr*100:+.3f}% OI+{oi_chg:.0f}%")

    save_alerted(alerted)

    if not alerts:
        elapsed = time.monotonic() - start
        print(f"[INFO] 扫描完成, 无命中 (耗时 {elapsed:.0f}s)")
        return

    for msg in alerts:
        broadcast(msg)
    print(f"[INFO] 已推送 {len(alerts)} 条扫描预警到 Telegram + 微信。")


if __name__ == "__main__":
    main()

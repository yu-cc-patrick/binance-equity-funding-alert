#!/usr/bin/env python3
"""监控币安下架公告: 发现涉及链上美股的下架 → 推送 Telegram + 微信。

数据源: Binance CMS 公告 API(catalogId=161=Delisting), 经 relay 中转。
只处理"去重"过的公告(code 去重, 持久化在 delist_state.json)。
"""
import json
import os
import re
import sys
import urllib.request

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DELIST_STATE = os.path.join(SCRIPT_DIR, "delist_state.json")

# —— 密钥读取(复用 monitor.py 的 get_secret) ——
import subprocess

KEYCHAIN_PREFIX = "binance-alert/"


def _load_dotenv():
    path = os.path.join(SCRIPT_DIR, ".env")
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


def _keychain_get(name):
    if not sys.platform.startswith("darwin"):
        return ""
    try:
        out = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_PREFIX + name, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def get_secret(name, default=""):
    val = os.environ.get(name)
    if val:
        return val
    val = _keychain_get(name)
    if val:
        return val
    return default


_load_dotenv()
BINANCE_BASE = get_secret("BINANCE_BASE", "https://fapi.binance.com").rstrip("/")
RELAY_TOKEN = get_secret("RELAY_TOKEN")
TG_TOKEN = get_secret("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = get_secret("TELEGRAM_CHAT_ID")
PP_TOKEN = get_secret("PUSHPLUS_TOKEN")

# —— 网络 ——


def http_get_json(path):
    url = BINANCE_BASE + path
    headers = {"User-Agent": "delist-monitor/1.0"}
    if RELAY_TOKEN:
        headers["X-Relay-Token"] = RELAY_TOKEN
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode("utf-8"))


# —— 推送(精简版, 复刻 monitor.py 的逻辑) ——


def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TG_CHAT_ID, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()


def send_wechat(text):
    if not PP_TOKEN:
        return
    title = text.split("\n")[0][:100]
    body = json.dumps({"token": PP_TOKEN, "title": title, "content": text, "template": "html"}).encode("utf-8")
    url = BINANCE_BASE + "/pushplus"
    headers = {"Content-Type": "application/json"}
    if RELAY_TOKEN:
        headers["X-Relay-Token"] = RELAY_TOKEN
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            resp.read()
    except Exception as e:
        print(f"[WARN] 微信发送失败(不重试, 下次 monitor 会补发): {e}")


def broadcast(text):
    send_telegram(text)
    send_wechat(text)


# —— 公告解析 ——

# 我们关注的币种 ticker(不含 USDT 后缀)
EQUITY_TICKERS = {
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA", "NVDA", "NFLX", "BABA",
    "AMD", "INTC", "COIN", "MSTR", "SNAP", "UBER", "DIS", "BA", "PYPL",
    "NKE", "SBUX", "MRNA", "PFE", "JPM", "GS", "V", "MA", "WMT", "COST",
    "ADBE", "CRM", "ORCL", "CSCO", "QCOM", "TXN", "AVGO", "ARM", "ASML",
    "PLTR", "SOFI", "RBLX", "U", "NET", "DDOG", "SNOW", "CRWD", "ZS",
    "PANW", "FTNT", "OKTA", "AI", "GTLB", "PATH", "HOOD", "AFRM", "SOUN",
    "RIVN", "LCID", "NIO", "XPEV", "LI", "BILI", "JD", "PDD", "TME",
    # 全量匹配靠 exchangeInfo, 此列表只是第一层关键词兜底;
    # 更准的过滤在下面 _is_equity_related()
}


def _is_equity_related(title, body):
    """判断公告是否涉及链上美股(基于标题+正文关键词)。"""
    text = (title + " " + body).upper()

    # 1. 直接提到美股 ticker(如 AAPL、MSFT)
    for t in EQUITY_TICKERS:
        if t in text:
            return True

    # 2. 提到"tokenized stock" / "equity" / "stock token" / "TRADIFI"
    keywords = ["TOKENIZED STOCK", "EQUITY", "STOCK TOKEN", "TRADIFI",
                "EQUITY PERPETUAL", "STOCK PERPETUAL"]
    for kw in keywords:
        if kw in text:
            return True

    # 3. 不含任何币种 ticker 的通用公告 → 可能是全平台公告,
    #    但若不含关键词则跳过
    return False


def _parse_delist_time(title, body):
    """从公告文本里粗提下架时间。Binance 格式通常:
    'at 2025-XX-XX XX:XX (UTC)' 或 'on 2025-XX-XX'
    """
    text = title + "\n" + body
    patterns = [
        r"at (\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*\(?UTC\)?",
        r"on (\d{4}-\d{2}-\d{2}\s+at\s+\d{2}:\d{2})",
        r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*\(?UTC\)?",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1) + " UTC"
    return "(未识别具体时间, 请查看公告全文)"


def _extract_products(body):
    """提取涉及的产品线: 现货/合约/杠杆..."""
    products = []
    text = body.upper()
    if any(w in text for w in ["SPOT", "现货"]):
        products.append("现货")
    if any(w in text for w in ["FUTURES", "PERPETUAL", "合约", "永续", "USDⓈ-M", "COIN-M"]):
        products.append("合约")
    if any(w in text for w in ["MARGIN", "杠杆", "全仓", "逐仓"]):
        products.append("杠杆")
    if any(w in text for w in ["EARN", "理财", "SIMPLE EARN"]):
        products.append("理财")
    return "、".join(products) if products else "现货/合约"


# —— 主逻辑 ——


def fetch_delisting_articles():
    """拉取最近 10 条下架公告(catalogId=161)。"""
    path = "/bapi/composite/v1/public/cms/article/list/query?type=1&catalogId=161&pageNo=1&pageSize=10"
    data = http_get_json(path)
    if data.get("code") != "000000":
        print(f"[ERROR] 公告 API 返回异常: {data.get('message', data)}", file=sys.stderr)
        return []
    # 数据结构: data.catalogs[0].articles
    catalogs = data.get("data", {}).get("catalogs", [])
    if not catalogs:
        return []
    return catalogs[0].get("articles", [])


def load_seen():
    if not os.path.exists(DELIST_STATE):
        return set()
    try:
        with open(DELIST_STATE, "r") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def save_seen(codes):
    with open(DELIST_STATE, "w") as f:
        json.dump(sorted(codes), f)


def main():
    try:
        articles = fetch_delisting_articles()
    except Exception as e:
        print(f"[ERROR] 获取下架公告失败: {e}", file=sys.stderr)
        sys.exit(1)

    if not articles:
        print("[INFO] 未获取到下架公告。")
        return

    seen = load_seen()
    new_codes = set()
    alerts = []

    for art in articles:
        code = str(art.get("code", ""))
        title = art.get("title", "")
        body = art.get("body", "") or art.get("description", "") or ""

        if not code or code in seen:
            continue
        new_codes.add(code)

        if not _is_equity_related(title, body):
            print(f"[INFO] 跳过(非美股相关): {title[:60]}")
            continue

        # 美股相关的下架公告！
        delist_time = _parse_delist_time(title, body)
        products = _extract_products(body)

        msg = (
            f"🚨 <b>币安下架公告</b>\n"
            f"标的: <b>{title[:120]}</b>\n"
            f"涉及产品: {products}\n"
            f"预计下架时间: {delist_time}\n"
            f"详情: https://www.binance.com/en/support/announcement/{code}"
        )
        alerts.append(msg)
        print(f"[ALERT] 美股下架: {title[:80]}")

    # 保存已见过的 code
    if new_codes:
        all_seen = seen | new_codes
        save_seen(all_seen)
        print(f"[INFO] 本轮新增 {len(new_codes)} 条公告({len(alerts)} 条美股相关)。")

    if not alerts:
        print("[INFO] 无新的美股下架公告。")
        return

    for msg in alerts:
        broadcast(msg)
    print(f"[INFO] 已推送 {len(alerts)} 条下架预警。")


if __name__ == "__main__":
    main()

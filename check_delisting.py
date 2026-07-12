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


def _get_equity_tickers():
    """获取当前链上美股 ticker。优先从 monitor 缓存的 equity_list.json
    读(免去 exchangeInfo 调用), 缓存不存在时才实时拉 exchangeInfo。
    """
    # 1. 先读 monitor.py 已经拉好并提交到仓库的缓存
    cache_file = os.path.join(SCRIPT_DIR, "equity_list.json")
    if os.path.exists(cache_file):
        try:
            with open(cache_file, "r") as f:
                cached = json.load(f)
        except (json.JSONDecodeError, OSError):
            cached = None
        if cached:
            # 兼容新格式({"symbols":[...],"updated_at":"..."}) 和旧格式(纯数组)
            symbols = cached if isinstance(cached, list) else cached.get("symbols", [])
            tickers = {s[:-4] for s in symbols if s.endswith("USDT")}
            if tickers:
                return tickers

    # 2. 无缓存时实时拉取
    try:
        info = http_get_json("/fapi/v1/exchangeInfo")
    except Exception as e:
        print(f"[WARN] exchangeInfo 拉取失败且无缓存: {e}")
        return set()
    tickers = set()
    for s in info.get("symbols", []):
        if s.get("underlyingType") != "EQUITY":
            continue
        if s.get("status") != "TRADING":
            continue
        sym = s.get("symbol", "")
        if sym.endswith("USDT"):
            tickers.add(sym[:-4])
    return tickers


def _is_equity_related(title, body, equity_tickers):
    """判断公告是否涉及链上美股(基于真实 EQUITY ticker 精确匹配)。"""
    # 用 uppercase 的文本, 按词边界匹配 ticker(避免 "AI" 匹配到 "MAIN" 等误报)
    text = (title + " " + body).upper()

    for t in equity_tickers:
        # ticker 必须是完整单词(前后是空格/标点/字符串边界)
        if re.search(r'\b' + re.escape(t) + r'\b', text):
            return True

    # 额外关键词: 明确提到 EQUITY perpetual / tokenized stock
    extra = ["TOKENIZED STOCK", "STOCK TOKEN", "EQUITY PERPETUAL",
             "STOCK PERPETUAL", "TRADIFI PERPETUAL"]
    for kw in extra:
        if kw in text:
            return True

    return False


def _extract_equity_tokens(title, equity_tickers):
    """从公告标题中找出涉及的美股 ticker(纯大写 2-5 字母)。"""
    text = title.upper()
    found = []
    for t in sorted(equity_tickers, key=lambda x: -len(x)):  # 长 ticker 优先匹配
        if re.search(r'\b' + re.escape(t) + r'\b', text):
            found.append(t)
    return found


def _parse_delist_date(title, body):
    """从公告文本里提取下架日期, 优先 2026-01-01 格式, 次选完整时间。"""
    text = title + "\n" + body
    # 优先找完整时间戳
    m = re.search(r"(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\s*\(?UTC\)?", text)
    if m:
        return m.group(1) + " UTC"
    # 日期兜底
    m = re.search(r"(\d{4}-\d{2}-\d{2})", text)
    if m:
        # 试着在后面或附近找时间
        return m.group(1)
    return "?"


def _extract_products(title, body):
    """提取涉及的产品线: 现货/合约/杠杆..."""
    text = (title + " " + body).upper()
    prods = []
    if any(w in text for w in ["FUTURES", "PERPETUAL", "合约", "永续", "USDⓈ-M", "COIN-M"]):
        prods.append("合约")
    if any(w in text for w in ["SPOT", "现货"]):
        prods.append("现货")
    if any(w in text for w in ["MARGIN", "杠杆", "全仓", "逐仓"]):
        prods.append("杠杆")
    if any(w in text for w in ["EARN", "理财", "SIMPLE EARN"]):
        prods.append("理财")
    return "、".join(prods) if prods else "现货/合约"


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
    # 先拿到当前链上美股清单(用于精确匹配)
    equity_tickers = _get_equity_tickers()
    if not equity_tickers:
        print("[ERROR] 无法获取链上美股清单, 退出。", file=sys.stderr)
        sys.exit(1)
    print(f"[INFO] 当前链上美股: {len(equity_tickers)} 个")

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

        if not _is_equity_related(title, body, equity_tickers):
            print(f"[INFO] 跳过(非美股): {title[:60]}")
            continue

        # 美股相关的下架公告！
        tokens = _extract_equity_tokens(title, equity_tickers)
        token_str = "、".join(tokens) if tokens else title[:60]
        delist_date = _parse_delist_date(title, body)
        products = _extract_products(title, body)

        msg = (
            f"🚨 {token_str} 预计 {delist_date} 下架{products}\n"
            f"如有长持小市值仓位请及时平仓\n"
            f"详情: https://www.binance.com/en/support/announcement/{code}"
        )
        alerts.append(msg)
        print(f"[ALERT] {token_str} → {delist_date} {products}")

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

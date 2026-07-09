#!/usr/bin/env python3
"""币安「链上美股永续」资金费率预警 -> Telegram + 微信

只监控币安 USDⓈ-M 上的 tokenized 股票永续(underlyingType == "EQUITY",
如 AAPLUSDT / MSFTUSDT), 其余加密币种忽略。监控对象由 exchangeInfo
自动识别, 新上市的美股永续会自动纳入, 无需维护清单。

两类预警(都只在"状态切换"时推送一次, 避免刷屏):
  1. 结算周期变化: 正常(8h/4h) <-> 极端(1h)
  2. 资金费率绝对值跨越 1% 阈值

在 GitHub Actions 上每 5 分钟运行一次。由于币安封锁美区 IP(451),
而 GHA runner 在美区, 因此请求经由非美区中转(relay)转发到币安。
状态持久化在 state.json, 由 GitHub Actions 提交回仓库。
通过 Telegram Bot + PushPlus 同时推送到 Telegram 和个人微信。
"""
import json
import os
import subprocess
import sys
import urllib.request

RATE_THRESHOLD = 0.01          # 1%
EXTREME_INTERVAL_HOURS = 1     # 周期降到 1h 视为极端
DEFAULT_INTERVAL_HOURS = 8     # 未在 fundingInfo 中的币种默认 8h

# 只监控这些 underlyingType 的标的(币安把链上美股标为 EQUITY)。
# 如需纳入韩股/盘前, 可加 "KR_EQUITY" / "PREMARKET"。
EQUITY_TYPES = {"EQUITY"}

# 状态文件放脚本同目录, 不依赖运行时的工作目录
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(SCRIPT_DIR, "state.json")
PENDING_FILE = os.path.join(SCRIPT_DIR, "pending_wx.json")
EQUITY_CACHE = os.path.join(SCRIPT_DIR, "equity_list.json")  # 美股清单缓存(防 exchangeInfo 间歇502)

# 与 funding-rate-alert 共用的 macOS Keychain 服务名前缀(本机取密钥用)
KEYCHAIN_PREFIX = "binance-alert/"


def _load_dotenv():
    """若同目录有 .env, 读入环境变量(不覆盖已存在的)。"""
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
    """从 macOS 钥匙串读取 binance-alert/<name>; 取不到返回 ""。

    仅在本机(darwin)尝试; GitHub Actions(Linux)上没有 security 命令,
    会静默跳过, 由环境变量(Secrets)兜底。
    """
    if not sys.platform.startswith("darwin"):
        return ""
    try:
        out = subprocess.run(
            ["security", "find-generic-password",
             "-s", KEYCHAIN_PREFIX + name, "-w"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return out.stdout.strip() if out.returncode == 0 else ""


def get_secret(name, default=""):
    """按优先级取密钥: 环境变量(GHA Secrets) -> Keychain(本机) -> .env -> default。

    _load_dotenv() 已把 .env 灌进环境变量, 所以 .env 也走 os.environ 这一层。
    """
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
WX_KEY = get_secret("PUSHPLUS_TOKEN")           # PushPlus 推送 Token(个人微信)


def http_get_json(url, retries=3):
    """GET + JSON + 自动重试(处理 relay 冷启动 + 币安间歇 502)。"""
    import time
    headers = {"User-Agent": "equity-funding-alert/1.0"}
    if RELAY_TOKEN:
        headers["X-Relay-Token"] = RELAY_TOKEN
    last_err = None
    for attempt in range(1, retries + 1):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            last_err = exc
            if attempt < retries:
                print(f"[WARN] {url.split('?')[0].rsplit('/',1)[-1]} 第{attempt}次失败({exc}), {3*attempt}s 后重试...")
                time.sleep(3 * attempt)
    raise last_err


def fetch_equity_symbols():
    """从 exchangeInfo 取出全部链上美股永续的 symbol 集合。

    拉成功则更新本地缓存(equity_list.json), 拉失败则用缓存兜底。
    """
    try:
        info = http_get_json(f"{BINANCE_BASE}/fapi/v1/exchangeInfo", retries=5)
    except Exception as e:
        print(f"[WARN] exchangeInfo 拉取失败({e}), 尝试读缓存...")
        cached = _load_equity_cache()
        if cached:
            print(f"[INFO] 使用缓存美股清单: {len(cached)} 个标的")
            return cached
        raise  # 无缓存则无法继续

    symbols = set()
    for s in info.get("symbols", []):
        if s.get("underlyingType") not in EQUITY_TYPES:
            continue
        if s.get("status") != "TRADING":
            continue
        sym = s.get("symbol", "")
        if sym.endswith("USDT"):
            symbols.add(sym)

    # 更新缓存
    _save_equity_cache(symbols)
    return symbols


def _load_equity_cache():
    if not os.path.exists(EQUITY_CACHE):
        return set()
    try:
        with open(EQUITY_CACHE, "r") as f:
            return set(json.load(f))
    except (json.JSONDecodeError, OSError):
        return set()


def _save_equity_cache(symbols):
    with open(EQUITY_CACHE, "w") as f:
        json.dump(sorted(symbols), f)


def fetch_market():
    """返回 {symbol: {"rate": float, "interval": int}}, 只含链上美股永续。

    不再依赖 premiumIndex(币安 418 限流该端点), 改为逐个调 fundingRate。
    """
    import time
    equities = fetch_equity_symbols()
    info = http_get_json(f"{BINANCE_BASE}/fapi/v1/fundingInfo")

    interval_map = {}
    for item in info:
        try:
            interval_map[item["symbol"]] = int(item["fundingIntervalHours"])
        except (KeyError, ValueError, TypeError):
            continue

    market = {}
    symbols = sorted(equities)
    n = len(symbols)
    fetch_start = time.monotonic()

    for i, sym in enumerate(symbols):
        try:
            data = http_get_json(
                f"{BINANCE_BASE}/fapi/v1/fundingRate?symbol={sym}&limit=1",
                retries=1,  # 单标的快速失败, 不拖累整体速度
            )
        except Exception:
            continue
        if not isinstance(data, list) or not data:
            continue
        try:
            rate = float(data[0]["fundingRate"])
        except (KeyError, ValueError, TypeError):
            continue
        market[sym] = {
            "rate": rate,
            "interval": interval_map.get(sym, DEFAULT_INTERVAL_HOURS),
        }
        # 50ms 间隔避免触发币安限流(每秒最多 20 个)
        if i < n - 1:
            time.sleep(0.05)

    elapsed = time.monotonic() - fetch_start
    print(f"[INFO] 逐标拉取费率完成: {len(market)}/{n} 个标的, 耗时 {elapsed:.0f}s")
    return market


def load_state():
    if not os.path.exists(STATE_FILE):
        return {}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2, sort_keys=True)


def _load_pending():
    """读取微信发送失败的积压消息列表。"""
    if not os.path.exists(PENDING_FILE):
        return []
    try:
        with open(PENDING_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def _save_pending(messages):
    """保存积压消息(覆盖)。"""
    if messages:
        with open(PENDING_FILE, "w", encoding="utf-8") as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)
    elif os.path.exists(PENDING_FILE):
        os.remove(PENDING_FILE)


def send_telegram(text):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("[WARN] 未配置 Telegram, 跳过推送:\n" + text)
        return
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": TG_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        resp.read()


def send_wechat(text):
    """通过 PushPlus 推送到个人微信。最多重试 3 次, 失败则存入积压队列。

    返回 True(成功)/False(失败并已积压)。
    """
    if not WX_KEY:
        print("[INFO] 未配置 PushPlus, 跳过微信推送。")
        return True  # 没配 key 不算失败, 不积压
    title = text.split("\n")[0][:100]
    payload_body = json.dumps({
        "token": WX_KEY,
        "title": title,
        "content": text,
        "template": "html",
    }).encode("utf-8")

    if BINANCE_BASE and BINANCE_BASE.startswith("https://"):
        url = BINANCE_BASE + "/pushplus"
        headers = {"Content-Type": "application/json", "User-Agent": "equity-funding-alert/1.0"}
        if RELAY_TOKEN:
            headers["X-Relay-Token"] = RELAY_TOKEN
        route = "relay"
    else:
        url = "https://www.pushplus.plus/send"
        headers = {"Content-Type": "application/json"}
        route = "直连"

    import time
    last_err = ""
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, data=payload_body, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            code = body.get("code", -1)
            if code == 200:
                print(f"[INFO] PushPlus({route}) 第{attempt}次成功 ({body.get('data','')})")
                return True
            else:
                print(f"[WARN] PushPlus({route}) 第{attempt}次 code={code} msg={body.get('msg','')}")
                last_err = f"code={code} {body.get('msg','')}"
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] PushPlus({route}) 第{attempt}次连接失败: {exc}")
            last_err = str(exc)
        if attempt < 3:
            time.sleep(5)

    # 3 次都不行 → 积压到文件, 下次补发
    pending = _load_pending()
    pending.append(text)
    _save_pending(pending)
    print(f"[ERROR] PushPlus 3 次均失败, 已积压(共 {len(pending)} 条)。最后错误: {last_err}")
    return False


def broadcast(text):
    """同时推送到 Telegram 和企业微信。"""
    send_telegram(text)
    send_wechat(text)


def pct(rate):
    return f"{rate * 100:+.4f}%"


def diff_and_alerts(market, prev_state):
    """对比新旧状态, 返回 (新状态, 预警消息列表)。"""
    new_state = {}
    alerts = []

    for symbol, data in market.items():
        rate = data["rate"]
        interval = data["interval"]
        is_extreme = interval <= EXTREME_INTERVAL_HOURS
        is_high = abs(rate) > RATE_THRESHOLD

        new_state[symbol] = {"extreme": is_extreme, "high": is_high}
        old = prev_state.get(symbol, {})
        old_extreme = old.get("extreme", False)
        old_high = old.get("high", False)

        # 1) 结算周期: 正常 -> 极端
        if is_extreme and not old_extreme:
            alerts.append(
                f"⚠️ <b>{symbol}</b> 进入极端状态\n"
                f"结算周期降至 <b>{interval}h</b>(当前费率 {pct(rate)})"
            )
        # 周期: 极端 -> 恢复
        elif not is_extreme and old_extreme:
            alerts.append(
                f"✅ <b>{symbol}</b> 恢复正常\n"
                f"结算周期回到 <b>{interval}h</b>(当前费率 {pct(rate)})"
            )

        # 2) 费率绝对值跨越 1% 阈值
        if is_high and not old_high:
            alerts.append(
                f"🔥 <b>{symbol}</b> 资金费率异常\n"
                f"当前 <b>{pct(rate)}</b> 超过 ±1%(周期 {interval}h)"
            )
        elif not is_high and old_high:
            alerts.append(
                f"🟢 <b>{symbol}</b> 费率回落\n"
                f"当前 <b>{pct(rate)}</b> 已回到 ±1% 以内"
            )

    return new_state, alerts


def flush_pending_alerts():
    """重发积压的微信消息。全部成功则清空, 有失败的保留。"""
    pending = _load_pending()
    if not pending:
        return
    print(f"[INFO] 发现 {len(pending)} 条积压微信消息, 尝试补发...")
    still_pending = []
    for i, msg in enumerate(pending, 1):
        if send_wechat(msg):
            print(f"[INFO] 积压 #{i} 补发成功")
        else:
            still_pending.append(msg)
    _save_pending(still_pending)
    if still_pending:
        print(f"[WARN] {len(still_pending)} 条仍然失败, 保留等待下次补发。")
    else:
        print("[INFO] 全部积压消息已补发完毕。")


def main():
    # 测试模式:设 TEST_MSG 环境变量则只发一条测试消息并退出
    test_msg = os.environ.get("TEST_MSG", "")
    if test_msg:
        broadcast(test_msg)
        print("[INFO] 测试消息已发送到 Telegram + 微信。")
        return

    # 先补发之前微信发送失败的积压消息
    flush_pending_alerts()

    try:
        market = fetch_market()
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] 获取行情失败: {exc}", file=sys.stderr)
        sys.exit(1)

    if not market:
        print("[ERROR] 未取到任何链上美股行情, 退出", file=sys.stderr)
        sys.exit(1)

    prev_state = load_state()
    first_run = not prev_state

    new_state, alerts = diff_and_alerts(market, prev_state)
    save_state(new_state)

    if first_run:
        # 首次运行只建立基线, 不把存量极端币种全推一遍刷屏
        print(f"[INFO] 首次运行, 已建立 {len(new_state)} 个美股标的基线, 不推送。")
        return

    if not alerts:
        print(f"[INFO] 共检查 {len(market)} 个美股标的, 无状态变化。")
        return

    wx_queued = 0
    for msg in alerts:
        send_telegram(msg)
        if not send_wechat(msg):
            wx_queued += 1
    wx_note = f"({wx_queued} 条微信已积压)" if wx_queued else ""
    print(f"[INFO] 已推送 {len(alerts)} 条预警到 Telegram + 微信。{wx_note}")


if __name__ == "__main__":
    main()

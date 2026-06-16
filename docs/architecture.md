# 系统结构与数据流

## 一句话

监控币安**全部链上美股永续**(underlyingType == EQUITY)的资金费率,出现极端情况时推送 Telegram。

## 与 funding-rate-alert 的关系

本项目是 [funding-rate-alert](../../funding-rate-alert) 的姊妹项目,**架构完全相同**,唯一差异在 `monitor.py` 的监控范围:

| | funding-rate-alert | 本项目 |
|---|---|---|
| 监控范围 | 全部 USDT 永续(~750) | 仅 `underlyingType==EQUITY`(~87) |
| 识别方式 | `symbol.endswith("USDT")` | 额外调 `exchangeInfo` 按 `underlyingType` 过滤 |
| relay | Render 新加坡 | **复用同一个**(透明转发,与币种无关) |
| Telegram / state / 触发条件 | — | 完全一致 |

## 组件

| 组件 | 位置 | 作用 |
|------|------|------|
| 监控脚本 | `monitor.py` | 识别美股标的、拉费率、对比状态、推送 |
| 定时器 | `.github/workflows/monitor.yml` | GitHub Actions cron,每 5 分钟触发 |
| 中转 relay | `relay/server.py` | 与 funding-rate-alert 共用的那个,绕币安美区封锁 |
| 状态文件 | `state.json` | 每个标的的"极端/超标"状态,由 Actions 提交回仓库 |

> 本项目**不含 `render.yaml`**:relay 已由 funding-rate-alert 部署,本项目直接复用,不再起第二份。

## 数据流

```
GitHub Actions runner (美区, 每 5 分钟)
   │  GET <BINANCE_BASE>/fapi/v1/exchangeInfo   (识别 EQUITY 标的)  ← 本项目新增
   │  GET <BINANCE_BASE>/fapi/v1/premiumIndex   (当前费率)
   │  GET <BINANCE_BASE>/fapi/v1/fundingInfo    (被调整的结算周期)
   │  header: X-Relay-Token
   ▼
Render relay (新加坡)  ──────►  fapi.binance.com (可达, 非美区)
   │                            ◄──────  JSON 响应
   ▼
monitor.py
   │  1. exchangeInfo 过滤出 underlyingType==EQUITY 的 USDT 永续
   │  2. premiumIndex/fundingInfo 解析: {symbol: {rate, interval}} (只留美股)
   │  3. 读 state.json (上一轮状态)
   │  4. 对比, 找出"状态切换"的标的
   │  5. 切换的才推 Telegram
   │  6. 写回 state.json
   ▼
git commit state.json  +  Telegram sendMessage
```

## 为什么用 underlyingType 识别美股(核心决策)

实拉 `exchangeInfo` 确认:币安把链上美股永续标为 `underlyingType == "EQUITY"`(AAPLUSDT、MSFTUSDT 均是),`contractType` 为独有的 `TRADIFI_PERPETUAL`,而普通币是 `COIN`。`EQUITY` 干净独立、新标的自动带上,因此用它做过滤,**不维护硬编码清单**。详见 [decisions.md](decisions.md)。

## 为什么需要 relay

与 funding-rate-alert 同因:币安合约接口对美国 IP 返回 451,而 GitHub Actions 免费 runner 在美区。引入非美区(Render 新加坡)中转代为访问。relay 只是透明 `/fapi/*` 转发,不关心币种,所以两个项目共用一份即可。

## 预警判定逻辑

对每个美股标的维护两个布尔状态,**仅在翻转时推一次**:

- `extreme` = 结算周期 ≤ 1h(`fundingInfo` 只返回被调周期的标的,其余默认 8h)
- `high` = `|fundingRate| > 1%`

**首次运行**只建基线、不推送。

## 配置项

`monitor.py` 顶部:`EQUITY_TYPES`(默认 `{"EQUITY"}`)、`RATE_THRESHOLD`(0.01)、`EXTREME_INTERVAL_HOURS`(1)、`DEFAULT_INTERVAL_HOURS`(8)。
环境变量:`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`、`BINANCE_BASE`、`RELAY_TOKEN`。

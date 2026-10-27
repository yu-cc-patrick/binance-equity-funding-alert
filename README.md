# binance-equity-funding-alert

> **一句话**：专门盯链上美股永续（AAPLUSDT、MSFTUSDT 等约 87 个标的）的资金费率异常，发生极端行情时推送 Telegram。
>
> **状态**：🟢 运行中 · 77 次提交 · 与 [funding-rate-alert](https://github.com/yu-cc-patrick/funding-rate-alert) 共用同一套架构和 relay
>
> **为什么单独做一个**：链上美股永续在传统市场休市时段缺少定价锚，费率行为和加密原生币完全不同——把它和普通永续混在一起监控，会漏掉这一类的结构性异常。

监控币安 USDⓈ-M 上**全部链上美股永续**(tokenized equity perp,如 AAPLUSDT / MSFTUSDT)的资金费率,出现极端情况时推送 Telegram 预警。

是 [funding-rate-alert](../funding-rate-alert) 的姊妹项目:同一套架构、同一个 relay、同样的两类触发条件,唯一区别是**监控范围**从「全部 USDT 永续」收窄到「`underlyingType == EQUITY` 的标的」。

## 监控对象

由币安 `exchangeInfo` 的 `underlyingType` 字段**自动识别**,凡是标为 `EQUITY` 且正在交易(TRADING)的 USDT 永续都纳入,新上市的美股永续自动加入,**无需维护清单**。当前约 87 个标的。

> 不含:`KR_EQUITY`(韩股)、`PREMARKET`(盘前)、`COMMODITY`、`INDEX`、`COIN`。如需纳入,改 [monitor.py](monitor.py) 顶部的 `EQUITY_TYPES`。

## 触发条件(两类,均只在状态切换时推一次,不刷屏)

1. **结算周期变化**:正常(8h/4h) ↔ 极端(1h)。币安在行情极端时会把周期降到 1h。
2. **费率绝对值跨越 ±1% 阈值**(超标 / 回落都推)。

调参在 [monitor.py](monitor.py) 顶部:`RATE_THRESHOLD=0.01`、`EXTREME_INTERVAL_HOURS=1`;cron 在 [.github/workflows/monitor.yml](.github/workflows/monitor.yml)。

## 架构

GitHub Actions(每 5 分钟)→ Render 新加坡 relay → 币安。relay 用来绕过币安对美区 IP 的 451 封锁。详见 [docs/architecture.md](docs/architecture.md)。

**relay 与 funding-rate-alert 共用同一个**(relay 只是透明 `/fapi/*` 转发,与币种无关),所以本项目不含 `render.yaml`,不需要再部署一份。

## 部署

1. 新建私有 GitHub 仓库,推上本目录。
2. 仓库 Settings → Secrets and variables → Actions,加 4 个 secret:
   - `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`(可与 funding-rate-alert 共用)
   - `BINANCE_BASE` = 你的 relay 地址（形如 `https://binance-relay-xxxx.onrender.com`）
   - `RELAY_TOKEN` = relay 端设的鉴权密钥(与 funding-rate-alert 一致)
3. Actions 页手动触发一次(workflow_dispatch),首轮建立基线、不推送。
4. 之后每 5 分钟自动跑,状态变化时推 Telegram。

## 密钥读取(三级回退)

`monitor.py` 按优先级取密钥:**环境变量 > macOS Keychain > `.env`**。一份代码两边都能跑:

- **GitHub Actions**:用仓库 Secrets(走环境变量),无需 `.env`。
- **本机**:推荐放 macOS Keychain,密钥不落任何明文文件。服务名前缀 `binance-alert/`(与 funding-rate-alert 共用):

```bash
security add-generic-password -a "$USER" -s "binance-alert/TELEGRAM_BOT_TOKEN" -w '你的bottoken' -U
security add-generic-password -a "$USER" -s "binance-alert/TELEGRAM_CHAT_ID"   -w '5862266477' -U
security add-generic-password -a "$USER" -s "binance-alert/BINANCE_BASE"       -w 'https://binance-relay-xxxx.onrender.com' -U
security add-generic-password -a "$USER" -s "binance-alert/RELAY_TOKEN"        -w 'relay密钥' -U
```

- **`.env`**:兜底/快速测试用,`cp .env.example .env` 后填值;已被 `.gitignore` 忽略。

## 本地运行

```bash
python3 monitor.py
```

注意:在美区网络(如本机)无法直连币安/Telegram,必须经 relay。`BINANCE_BASE` 指向 relay 即可。

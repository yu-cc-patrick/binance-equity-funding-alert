# AI 修改记录

## 2026-06-15 — 项目初建

**来源:** fork 自 [funding-rate-alert](../../funding-rate-alert),需求为「监控币安全部链上美股永续的资金费率,触发条件与原项目一致」。

**做了什么:**
- 搭目录结构,复用原项目的 `relay/server.py`、`.gitignore`。
- 实拉 relay 的 `exchangeInfo` 确认链上美股标识 = `underlyingType == "EQUITY"`(见 decisions ADR-001)。
- 重写 `monitor.py`:新增 `fetch_equity_symbols()` 调 `exchangeInfo` 过滤,`fetch_market()` 只保留美股标的;其余(telegram/state/diff/触发条件)与原项目逐字一致。
- workflow 改名 `binance-equity-funding-alert`,commit message 改 `equity funding state`;不含 `render.yaml`(复用原 relay,ADR-002)。
- 加 `.devcontainer/`(Dockerfile + devcontainer.json + init-firewall.sh):防火墙白名单按本项目出站需求设为 relay + Telegram + GitHub + pypi + anthropic;Dockerfile 用 py3.12、去掉原项目的 matplotlib 依赖(本项目纯标准库)。
- 写 README / docs(architecture、decisions、本文件)。

**验证:** 用真实 `exchangeInfo` fixture 离线跑过滤逻辑 —— 87 个 EQUITY 全中、BTCUSDT 等排除、字段完整、`market ⊆ equities`。Telegram 推送与 GHA 全链路未在本机验证(美区网络无法直连,需部署后在 Actions 上手动触发确认)。

**待办(交给用户):**
- 新建私有 GitHub 仓库并推送。
- 配 4 个 Secrets(TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID / BINANCE_BASE / RELAY_TOKEN)。
- Actions 手动触发首轮建基线,再观察后续推送。
- **安全:** 本次对话暴露了 RELAY_TOKEN,建议在 Render + GitHub Secrets 两边轮换。

## 2026-07-10 — 预警增强：持续时间 / BTC联动 / 周末标记

**来源:** 用户问「链上美股夜间交易能不能预判白天走势」，分析后决定为预警增加上下文维度。

**做了什么:**

1. **持续时间追踪** — `state.json` 新增 `high_duration` / `extreme_duration` 两个计数器。
   - 每轮如果状态持续触发 +1，恢复归零。
   - 触发时显示「首次触发」vs「已持续 N 周期」，恢复时显示「持续了 N 个周期后回落」。
   - 旧 state 向后兼容（`.get()` 缺省为 0）。

2. **BTC 联动判断** — 新增 `fetch_btc_rate()`，每轮同时拉 BTCUSDT 资金费率。
   - 美股标的 |费率| > 1% 且 BTC 同步同向极端 → 标记 `[加密联动]`
   - 美股标的 |费率| > 1% 但 BTC 正常 → 标记 `[独立异动]`
   - BTC 获取失败不影响主流程，仅跳过标记。

3. **周末特殊标记** — 新增 `is_weekend()`（UTC 周六/日），触发预警时加 `[WEEKEND]` 标签。
   - 周末指数价格约束更紧（±3%），此时触发的极端费率信号更纯粹。

4. **预警消息格式变更** — 上下文标签前缀、持续时间后缀。例：
   ```
   [WEEKEND|独立异动] 🔥 AAPLUSDT 资金费率异常
   当前 +1.2345% 超过 ±1%(周期 8h) 首次触发
   ```

**验证:**
- ✅ 本地完整链路测试：`python3 monitor.py` 不走 test_msg，relay→币安全链路通。
  - exchangeInfo 拉取成功（relay 冷启动后重试），112 个标的全量拉取（耗时 432s）。
  - BTCUSDT 费率获取成功（+0.0091%）。
  - state.json 格式验证：112 个标的四个字段齐全。
- ⬜ 线上完整 pipeline 测试（需用户触发 workflow_dispatch 确认 GHA 上也能跑通）。

  **线上验证（2026-07-11 workflow_dispatch）:**
  - ✅ GHA 全链路通过：117 个标的全量拉取（耗时 44s）。
  - ✅ BTCUSDT 费率获取成功（+0.0009%）。
  - 无触警（当前市场无 |费率| > 1%），三项新功能代码路径就位，只待实际触警验证。

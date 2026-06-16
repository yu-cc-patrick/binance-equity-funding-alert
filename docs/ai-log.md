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

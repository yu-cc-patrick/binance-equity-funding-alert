# 架构决策记录

## ADR-001:用 `underlyingType == "EQUITY"` 识别链上美股

**日期:** 2026-06-15

**背景:** 需要从币安全部 ~790 个 USDⓈ-M 永续里,自动挑出"链上美股"(tokenized equity perp),且新上市的美股要自动纳入,不靠人工维护清单。

**调研(实拉 relay 的 `exchangeInfo` 确认):**
- 链上美股(AAPLUSDT、MSFTUSDT)的 `underlyingType == "EQUITY"`,`contractType == "TRADIFI_PERPETUAL"`,`underlyingSubType == ["TradFi"]`。
- 普通币 `underlyingType == "COIN"`。
- 全量分布:`COIN` 689、`EQUITY` 87、`COMMODITY` 8、`KR_EQUITY` 3、`INDEX` 3、`PREMARKET` 2。

**决策:** 以 `underlyingType in {"EQUITY"}` 为过滤条件。

**理由:**
- `EQUITY` 是币安官方字段,语义明确、独立,比按 symbol 前缀猜可靠。
- 新美股上市自动带 `EQUITY`,零维护。
- 韩股(`KR_EQUITY`)、盘前(`PREMARKET`)默认排除,符合"链上美股"的字面范围;需要时改 `EQUITY_TYPES` 即可扩展。

**验证:** 用真实 `exchangeInfo` fixture 跑 `fetch_equity_symbols()`,87 个 EQUITY 全部命中,BTCUSDT 等普通币全部排除,`market ⊆ equities` 成立、无遗漏。

---

## ADR-002:复用 funding-rate-alert 的 relay,不另起一份

**日期:** 2026-06-15

**背景:** 本项目同样需要绕过币安美区 451 封锁。

**决策:** 直接复用 funding-rate-alert 已部署的 Render 新加坡 relay(`binance-relay-jaeb.onrender.com`),本项目不含 `render.yaml`。

**理由:**
- relay 是透明 `/fapi/*` 转发,不关心币种,一份能同时服务两个监控。
- 省一份部署和维护;Render 免费实例够用(每 5 分钟一次请求,保活足够)。
- 代价:两个项目的请求叠加到同一 relay,但量级极小(每轮各几次 GET),无压力。

**权衡:** 若未来某项目对 relay 可用性要求变高,可再拆独立 relay。当前共用最省。

---

## ADR-003:fork 代码而非抽公共库

**日期:** 2026-06-15

**背景:** 本项目与 funding-rate-alert 的 telegram/state/relay/触发逻辑几乎一样。

**决策:** 整体 fork、各自独立一份代码,只改 `fetch_market` 的过滤;暂不抽公共库。

**理由:** 这是第二个用这套逻辑的项目。两份独立代码更解耦,改一个不怕碰坏另一个。等到第 3、第 4 个项目复用同一套(telegram+state+relay)时,再由真实重复驱动抽 `binance-monitor-core`,届时抽象形状也更清楚。**抽象由真实重复驱动,不由预测驱动。**

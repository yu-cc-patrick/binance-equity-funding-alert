# CLAUDE.md

## 1. Project Purpose

监控币安 USDⓈ-M 上**全部链上美股永续**(`underlyingType == EQUITY`,如 AAPLUSDT/MSFTUSDT)的资金费率,在出现极端情况(结算周期降到 1h、或 |费率|>1%)时推送 Telegram 预警。

是 `funding-rate-alert` 的姊妹项目:同架构、同 relay、同触发条件,唯一差异是监控范围收窄到链上美股。

目标:**可维护 / 可恢复理解 / 可安全迭代**。

## 2. Architecture

系统结构与数据流见 [docs/architecture.md](docs/architecture.md)。
一句话:GitHub Actions(每 5 分钟)→ Render 新加坡 relay → 币安。relay 复用 funding-rate-alert 那一份,绕过币安美区 451 封锁。

## 3. Core Design Rules

- 优先可读性 > 优化
- 模块独立,禁止隐式耦合
- 不留临时测试代码
- 所有关键行为必须可追溯
- 与 funding-rate-alert 共用的部分(relay/telegram/state 逻辑)保持一致,改动需同步考虑两个项目

## 4. AI / Claude 修改规则

每次修改代码后必须:
- 更新相关文档(如有影响)
- 删除 debug / 临时代码
- 保持主流程可运行
- 重要决策记录到 [docs/decisions.md](docs/decisions.md),改动记录到 [docs/ai-log.md](docs/ai-log.md)

## 5. Debug / Recovery Strategy

重新接手项目时按此顺序:
1. 看 README.md
2. 看 docs/architecture.md
3. 看 docs/decisions.md
4. 跑一次完整流程(Actions 手动触发 / 本地经 relay 验证)
5. 再看代码细节

## 6. Documentation Index

- [docs/architecture.md](docs/architecture.md) → 系统结构 + 数据流
- [docs/decisions.md](docs/decisions.md) → 架构决策记录(美股识别、relay 复用、fork vs 抽库)
- [docs/ai-log.md](docs/ai-log.md) → AI 修改记录
- [README.md](README.md) → 使用说明

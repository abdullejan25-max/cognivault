# 2026-10-31 通用自动回忆设计检查点

## 用户目标与范围

正常提问时自动回忆相关记录，不需要点名检索。用户明确要求覆盖所有能够接入项目的 Agent，并追加 DSH 一起；Codex、WorkBuddy、Hermes、DSH 是首批适配与验收对象，不是支持范围白名单。

## 本轮交付

- `DONE`：检查当前源码、Gateway 联合检索合同、各 Host setup、隐私边界与当前基线。
- `DONE`：[设计草案](../superpowers/specs/2026-10-31-universal-automatic-recall-design.md)，采用通用召回核心、正式 Gateway 调用、宿主适配与统一验收。
- `DONE`：区分 MCP 连接、模型主动工具调用和宿主自动召回；未来客户端需要适配与验收，不能凭接入 MCP 宣称自动回忆。
- `PARTIAL`：适配入口调查。Codex 官方 hook 合同与本机 CLI hooks 开启状态已核实；WorkBuddy 桌面行为、Hermes 自定义 provider 和 DSH 当前宿主合同尚待核验。
- `NOT RUN`：实现代码、单元/集成测试、四端原生自动回忆、生产检索及生产启用。本轮仅变更文档，不需要运行与变更无关的业务测试。

## 当前连接证据

当前 Host 对 `cognivault` 已知 URI 的直接资源读取返回 `unknown MCP server 'cognivault'`，该工作树没有本机 `.codex/config.toml` 或 `config.local.toml`。这说明本次聊天无法通过该原生入口读取，不说明生产数据不存在或已损坏。不使用临时 MCP 覆盖、直连客户端或私人数据库读取来替代。

## 续跑边界

设计等待用户审阅。审阅后进入实现计划，先核实正式接入与四端触发能力，再执行隔离实现和逐端验收。保留现有权威数据、写入权限和配置；不自动采集、不启用记忆写入、不发布。

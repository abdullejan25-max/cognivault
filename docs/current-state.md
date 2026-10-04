# Current State

最近核对：2026-10-04。本页是当前运行与发布状态的主要依据。当前正式稳定版本为 **[v0.7.0 — History Completion & Recovery](releases/v0.7.0.md)**；下列 acquisition 与 recovery closure 统计在该版本首次发布后完成。

## CogniVault identity transition

当前产品、Python distribution/import package 与标准 MCP registration 分别为 **CogniVault** / `cognivault`。仓库为 [abdullejan25-max/cognivault](https://github.com/abdullejan25-max/cognivault)。版本仍为 `0.7.0`，此次内部改名未创建新 tag 或 Release。

升级应使用全新环境，或先卸载旧 distribution `chatgpt-study-system-v2` 再安装 `cognivault`；二者共用旧 stdio shim 文件和 `study-migrate` launcher。先安装新包再卸载旧包会删掉这两个共享文件，旧 regular package 也可能遮蔽新 editable shim。详细步骤见 [README](../README.md#upgrade-from-the-former-distribution)。Codex helper 只自动替换精确识别的旧生成配置；Hermes、WorkBuddy 和自定义 Codex 配置需手动改 registration 与 module path，保留原 backend/capabilities。

下列计数、恢复与 Host PASS 均是更名前已记录的证据，本页未重新读取生产数据。改名后的真实 Host 与存量生产数据兼容性尚未验收；自动化或 synthetic PASS 不会补足这两项门禁。历史 checkpoint / Release Notes 保留原项目名、命令和 `study_system` aliases，作为当时证据。

## Current release status

| 指标 | 最终已取得数据 closure |
| --- | ---: |
| Sources / outcomes | 1,804 |
| Canonical conversations（规范化对话） | 487 |
| Distinct canonical messages（去重消息） | 7,334 |
| 完全 source-only 的记录 | 1,351 |
| 有界 V1 原始输入 | 443 |
| V1-only unknown | 0 |

Source-only 记录保留原始来源证据；没有足够依据生成的 canonical messages 不会被补造。详细对账见 [P13 checkpoint](p13-history-completion-checkpoint.md)。

## Host status

| Host | 更名前已记录的证据 |
| --- | --- |
| Codex | Codex Desktop P13 History 原生 MCP 读取与无结果检查：**PASS**。 |
| Hermes | P13 History 原生 MCP 读取与无结果检查：**PASS**。 |
| WorkBuddy | P12 integration 仍为 **PASS**；P13 History 专项 GUI verification 为 **DEFERRED**。 |
| ChatGPT hosted | Hosted MCP / Secure MCP Tunnel **尚未实现**；官方 ChatGPT export 为 **`acquisition_pending`**。 |

WorkBuddy 的 P12 结果不代表 P13 History GUI 验收。另有一项生产 writable ingress 的正式 Gateway synthetic acceptance 已通过；该结果不代表真实用户数据验收。Host 证据见 [P12 checkpoints](p12-step4-cross-agent-checkpoint.md) 和 [production ingress 报告](production-ingress.md)。

## History acquisition

- 已取得的输入以 source evidence 形式保留。Canonical conversations 与 messages 是确定性生成的派生层，不替换或改写原始证据。
- 只有来源结构、角色和顺序有可靠依据时才执行规范化；含糊或不支持的记录保留为 source-only。
- ChatGPT 官方 export 仍是 `acquisition_pending`。取得后计划增量、幂等补录；完成前不表述为已获取。
- 稳定处理约定见 [source ingestion](history-source-ingestion.md) 与 [canonical normalization](history-normalization.md)。

## Recovery

以下两项恢复检查对应不同阶段与范围，文件数和字节数不能合并计算。

| 恢复阶段 | 结果 | 范围 |
| --- | --- | --- |
| 早期完整备份与 isolated restore | **PASS_NATIVE** — 3,778 files / 44,380,473,111 bytes | 当时的完整静态快照，包含 Study/QMD 和已配置的数据域。 |
| 最终 mutable supplement 与 isolated restore | **PASS_NATIVE** — 247 files / 1,049,088,134 bytes | 覆盖最终获取数据变化、ledger 和 mutable-domain readback；明确排除 Study，与早期完整备份配套。 |

最终 mutable restore 验证 source/canonical digests、provenance、ledger equality，以及恢复后经 Gateway 对 mutable domains 的正式读取。Isolated restore 副本用于验证恢复能力；Study 的权威数据仍由原 StudyVault 提供。恢复流程与边界见 [private recovery](p13-recovery.md)。

## V1 retirement

- Unique-data audit 为 **PASS**：443 个有界原始输入全部有映射，`V1-only unknown = 0`。
- V1 已逻辑退役；原始数据库、来源和 cold archives 仍保留。
- Legacy Basic Memory writer hooks 已可恢复地停用，legacy collector 入口也有防止重新启用的保护。
- 尚未执行物理删除；删除必须另获 owner 明确确认。
- StudyVault 仍是 Study 的唯一权威来源。V1 退役和 isolated recovery 不会建立第二份 Study 权威数据。

退役审计与删除边界见 [V1 retirement evidence](p13-v1-retirement.md)。

## Current limitations

- 当前 MCP transport 是本机 stdio；Hosted ChatGPT MCP 和 Secure MCP Tunnel 尚未实现。
- ChatGPT 官方 export 尚未到达，获取仍待处理。
- WorkBuddy 的 P13 History 专项 GUI verification 仍为 deferred；既有 P12 integration 结果有效。
- Caller / Agent identity 为 `reported / unverified`，不等于身份认证。
- Study 搜索需用户选装 QMD 和 Node.js；OCR 是可选能力，需要 Tesseract 和语言数据。OCR 文本属于派生数据，不能代替原始证据。

## Historical milestones

以下仅概述各版本范围；详细验收证据保留在对应历史 checkpoint 与 Release Notes 中。

- **v0.2.0** — V1 来源迁移与 V2 cutover；[P11 completion](p11-real-migration-completion.md)。
- **v0.3.0** — Obsidian projection；[P12 Step 1](p12-step1-real-projection-checkpoint.md)。
- **v0.4.0** — WorkBuddy integration；[P12 Step 2](p12-step2-workbuddy-checkpoint.md)。
- **v0.5.0** — Hermes integration；[P12 Step 3](p12-step3-hermes-checkpoint.md)。
- **v0.6.0** — Cross-Agent integration；[P12 Step 4](p12-step4-cross-agent-checkpoint.md)。
- **v0.7.0** — History completion and recovery；[Release Notes](releases/v0.7.0.md) 与 [P13 checkpoint](p13-history-completion-checkpoint.md)。

稳定组件职责见 [Architecture](architecture.md)，本机数据处理规则见 [Privacy Boundary](privacy-boundary.md)。

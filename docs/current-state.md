# Current State

最近核对：2026-10-05。本页是当前运行与发布状态的主要依据。当前正式稳定版本为 **[v0.8.0 — CogniVault Identity & Reliability](releases/v0.8.0.md)**。本次发布汇集 identity transition、CI 与 integrity verifier 修复；下列历史 acquisition 与 recovery closure 属于 v0.7.0 completion 范围，没有作为 v0.8.0 新采集结果。

## CogniVault identity transition

当前产品、Python distribution/import package 与标准 MCP registration 分别为 **CogniVault** / `cognivault`，软件版本为 `0.8.0`。仓库为 [abdullejan25-max/cognivault](https://github.com/abdullejan25-max/cognivault)。Identity transition 没有迁移或改写权威数据。

升级应使用全新环境，或先卸载旧 distribution `chatgpt-study-system-v2` 再安装 `cognivault`；二者共用旧 stdio shim 文件和 `study-migrate` launcher。先安装新包再卸载旧包会删掉这两个共享文件，旧 regular package 也可能遮蔽新 editable shim。详细步骤见 [README](../README.md#upgrade-from-the-former-distribution)。Codex helper 只自动替换精确识别的旧生成配置；Hermes、WorkBuddy 和自定义 Codex 配置需手动改 registration 与 module path，保留原 backend/capabilities。

历史计数、恢复与 Host PASS 保留其原始检查点范围；本页另列 2026-10-05 的改名后只读 Gateway 验收。历史 checkpoint / Release Notes 保留原项目名、命令和 `study_system` aliases，作为当时证据。

## Historical v0.7.0 acquired-data closure

| 指标 | 最终已取得数据 closure |
| --- | ---: |
| Sources / outcomes | 1,804 |
| Canonical conversations（规范化对话） | 487 |
| Distinct canonical messages（去重消息） | 7,334 |
| 完全 source-only 的记录 | 1,351 |
| 有界 V1 原始输入 | 443 |
| V1-only unknown | 0 |

这些是 v0.7.0 首次发布后完成的历史 closure 数字；当前 live 计数另见下节，不混合时间范围。Source-only 记录保留原始来源证据；没有足够依据生成的 canonical messages 不会被补造。详细对账见 [P13 checkpoint](p13-history-completion-checkpoint.md)。

## Host status

| Host | 已记录的证据 |
| --- | --- |
| Codex | 更名前 Codex Desktop P13 History 原生 MCP 读取：**PASS**；2026-10-05 CogniVault v0.8.0 RC fresh CLI Host 的 health 与 History integrity read：**PASS**；当日此前的改名后 Study/QMD 无结果搜索：**PASS**。 |
| Hermes | P13 History 原生 MCP 读取与无结果检查：**PASS**。 |
| WorkBuddy | P12 integration 仍为 **PASS**；P13 History 专项 GUI verification 为 **DEFERRED**。 |
| ChatGPT hosted | **OPTIONAL / DEFERRED BY OWNER CHOICE**。Secure MCP Tunnel 技术指南与工程准备保留；没有 live tunnel / ChatGPT app E2E，不属于 v0.8.0 release Gate；官方 ChatGPT export 为 **`acquisition_pending`**。 |

WorkBuddy 的 P12 结果不代表 P13 History GUI 验收。另有一项生产 writable ingress 的正式 Gateway synthetic acceptance 已通过；该结果不代表真实用户数据验收。Host 证据见 [P12 checkpoints](p12-step4-cross-agent-checkpoint.md) 和 [production ingress 报告](production-ingress.md)。

## Live Gateway integrity verification

2026-10-05，v0.8.0 RC 的 fresh Codex CLI Host 经 `cognivault` MCP 只读配置再次完成 health、source snapshot、canonical summary 与 integrity verification，Gateway reported version 为 `0.8.0`。Live summary 为 1,805 个 sources、541 个 conversations、8,599 条 messages、548 个 views 和 1,806 个跨版本 outcomes；verifier 对 8,599 条 messages 与 1,805 个当前版本 outcomes 返回 **verified**、0 diagnostics。Source-set 与 canonical digests 和当日发布准备前的验证相同。此次使用默认 verification，没有重新解析原始来源；当日此前的 Study/QMD bounded 无结果查询返回空集合，没有将无结果解释为数据缺失。

此前 1,265 条 `source_evidence_mismatch` 全部是 `version` 字段差异。Gateway 原生创建并 isolated restore 的只读副本与 live store 的 source-set digest、canonical summary 和 canonical digest 完全匹配；带原 verifier 的 Gateway 在该副本再次返回同样的 1,265 条版本差异，均落在一个 ChatGPT source 的 54 个历史 views 中。历史 message evidence 的版本与其所属 view 版本一致，verifier 错误地把所有历史版本都与当前全局版本比较。这是 **A：verifier bug**，不是 source bytes、canonical 内容或 provenance 损坏。

Verifier 现在按每条 evidence 所属的 view 版本检查 `version`，并保留其他 source evidence 字段校验。合成 regression 在修复前复现 `source_evidence_mismatch`、修复后通过；真实 production store 仍为只读，未重新 ingest、normalize 或修改任何记录。当前代码通过 live Gateway 返回 **verified**，因此不需要数据修复。

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

- 2026-10-06 的 Windows 安装向导已完成开发验收，当前源码提供根目录 `install.cmd` 入口，详见 [安装说明](installation.md)与[安装向导 checkpoint](install-wizard-checkpoint.md)。正式 v0.8.0 标签不包含该入口，新用户应下载包含安装器的当前源码。
- CI workflow 已启用，Ubuntu core/full tests、Ubuntu package build/install 与 Windows package/integrity/MCP compatibility 三个 jobs 均已通过；README 的 CI badge 跟踪 `main` 分支状态。
- 当前正式 transport 是本机 stdio MCP。ChatGPT Web / Secure MCP Tunnel 为 **OPTIONAL / DEFERRED BY OWNER CHOICE**，不属于 v0.8.0 release Gate，也不要求为本次发布完成 OpenAI 账号授权。[技术指南](chatgpt-integration.md)保留供未来选用；没有 live tunnel / ChatGPT app E2E。
- 2026-10-05 的 tunnel 工程准备仅包括官方 Windows client v0.0.15 的 SHA-256 校验、quickstart 调查和 synthetic profile generation；未启动 daemon、doctor 或连接账号。
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
- **v0.8.0** — CogniVault identity transition、CI 与 integrity reliability；[Release Notes](releases/v0.8.0.md)。

稳定组件职责见 [Architecture](architecture.md)，本机数据处理规则见 [Privacy Boundary](privacy-boundary.md)。

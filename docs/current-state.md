# Current State

最近核对：2026-11-02（自动回忆续验）。本页记录源码与运行验收范围；当前正式稳定发行版须以[GitHub最新Release](https://github.com/abdullejan25-max/cognivault/releases/latest)及其标签核验，不能从metadata推断。历史[v0.8.0](releases/v0.8.0.md)汇集identity transition、CI与完整性修复；下列acquisition/recovery closure属于v0.7.0 completion范围，不是后续版本新采集结果。

## 通用自动回忆（开发分支，2026-10-31）

11月2日最新产品源 `0aefced` 完整隔离自动测试为 **1702 passed, 18 skipped，522.89秒**，显式使用官方Hermes runtime执行manifest检查。`900b7fc`澄清来源作者、未核实当前断言和版本演变；`0aefced`增加可信非穷尽检索标记，明确`COMPLETE`不是全库覆盖、截断结果不是全文读取。两轮修正均独立审查通过，新增外框仍占原8000码点预算；指导文字不保证模型服从。新Flash原生七案自动层通过，但答案仍有PARTIAL/FAIL，旧失败均保留。

同一产品源随后在独立环境启用锁定的`pdf-ocr`和本机官方QMD 2.8.3，补跑原先跳过的PDF二进制资源及三项纯合成QMD运行时检查：**4 passed，41.10秒**。补跑不改写上述整套计数；未运行涉及个人真实资料的QMD smoke。DSH官方Pro路由单题探针已实际交付3783码点、三条History，答案正确识别合成来源及修订后失效、保留完整引用；末尾却提出当前只读能力不支持的写入提议，未实际写入，不能判整体语义通过。随后固定Pro/high七个全新原生session均完成，独立审查为自动触发交付层通过、答案 **3 PASS / 1 PARTIAL / 3 FAIL**；批准未提交和冲突仍被错误归属真人当前事实，英文题仍有范围越界及检索不可用漏报。更换模型没有关闭原质量门槛，不发布新正式版。

当前Desktop入口已取得真实证据：正式queue不加CLI resume，经App消费后产生原生用户事件及作答前hook。另以公开fork/revert形成持久有效空上下文，实际Desktop首轮COMPLETE2、一次请求、125ms，无模型主动工具调用，答案正确引源并标明合成来源；保留fork谱系，不冒称没有谱系的新线程。DSH官方ACP也已通过同进程共享Gateway的检索中途定向取消、B继续、同A新轮重新取证，独立审查仅判Host/调用层PASS，服务端内部物理中止未验证。完整质量矩阵与WorkBuddy可部署合同仍未闭合，整体仍PARTIAL；版本仍0.9.1，未发布新正式版。以下历史计数保留各自源码范围，详见实施检查点。

已增加宿主侧共用 Recall Core、显式只读 MCP 连接、Codex 持久宿主适配、WorkBuddy command hook、DSH native JSON 桥及 Hermes 通用接口；没有把规则规划放入 Gateway，也没有增加自动写入。`7e701de` 基线完整隔离回归 **1643 passed, 18 skipped**，Core 与宿主边界的独立审查问题均已修复。合成 SDK 已验证持久连接、新版本/失效、请求去重与适配服务进程重启，不代表客户端重启或真实模型使用。

整体仍为 **PARTIAL**，四端均未判完整 `AUTO`。Hermes 已完成独立登录、Windows 长路径修复、真实 CLI/模型首请求自动交付、引源/时序及实际进程重启；DSH 已在独立 profile 通过正式装载、原生 registry 与账号模型自动取证/引源，PTC 实测不支持。WorkBuddy 的真实提问 hook 已触发，但个人召回未完成连接且隔离认证受阻；Codex 用户完成精确 hook 信任后，已通过正式 CLI 开启原本关闭的已信任钩子（Installed 1 / Active 1）；真实 TUI 首次问题与实际退出重启均自动交付完整两条证据并正确引源，另已核实算式/翻译零召回、无资料披露及来源攻击；学习建议引源正确但Study覆盖不足。未代写 trust。f026eb7 根据真实模型反例补齐引用/时序/信任合同，修复时聚焦回归 **136 passed, 1 skipped**、独立审查通过；此前全套 **1678 passed, 18 skipped** 不包含此轮新增用例。2026-11-01 续验修复测试时序不稳定和 DSH Unicode 计数不一致，`0707e2c` 后独立聚焦回归 **137 passed, 1 skipped**，最终分支审查 APPROVED。Hermes 额外主动调用成本、DSH 学习用例的传输/引用 ID 问题与剩余原生矩阵均如实保留。首批四端不是支持白名单。详见[接入说明](automatic-recall.md)、[统一验收](automatic-recall-acceptance.md)及[实施检查点](checkpoints/2026-10-31-universal-automatic-recall-implementation.md)。未启用生产、未推送或发布。

正式发布收尾已修复两项可复现问题：`fc22fe0` 将 DSH/Hermes 原生插件按单一源码打入 wheel，实际 wheel/sdist 内容与隐私审计、干净安装及资源字节核对通过；`a19708a` 修复显式英文学习主题丢失，Core 与联合证据回归 **79 passed**，独立复审通过。该提交完整回归 **1692 passed, 18 skipped，637.02秒**，显式使用官方 Hermes runtime 执行 manifest 检查。版本仍为 `0.9.1`，这些开发分支结果不是新的正式 Release。DSH 英文原生用例确认首次模型请求前交付5条证据（含2条Study），但带完整metadata的新运行答案缩短引用ID，引源FAIL保留；无主题泛问的Study字面检索局限、当前Desktop、高级原生矩阵和WorkBuddy首轮连接/取消传播门槛尚未关闭。

## v0.9.1 Memory 并发修复（2026-10-31）

当前源码修复 Memory 对 SQLite 临时日志文件多次读取元数据产生的竞态，保留硬链接、符号链接、Windows reparse、父路径和访问错误保护。不涉及 schema、真实数据迁移或权限扩展。首次失败记录见[项目测试](checkpoints/2026-10-31-project-test.md)，修复与发行边界见 [v0.9.1](releases/v0.9.1.md)。本次代码与隔离测试不代表当前 Desktop 原生或生产验收通过。

## v0.9.0 Agent Orchestration 源码与验收（2026-10-26）

最新续跑通过独立重启助手完成Host重载，并实际调用admin-only `initialize_history`，完成原生导入、规范化与reparse核验。History/Memory首次创建的journal竞态已修复。三域任务及联合检索均已有真实B与当前原生工具回执；Memory仅为pending历史候选。详见[最新三域检查点](checkpoints/2026-10-26-v090-native-three-domain-closeout.md)和[首次部署修复](checkpoints/2026-10-26-v090-first-deployment.md)。实际发行状态以最终提交Checks及GitHub Releases为准，历史PARTIAL记录不等于当前发布状态。

源码和包metadata为 `0.9.0`；正式发行须核验对应Release与tag，版本号本身不能证明发布。原编排SDK增加可信依赖计划、成本/延迟估计筛选与停用、结构化执行器错误、认证阻塞恢复和取消边界修复。`HarnessNoToolsAdapter` 使用独立cwd/home、仅模型/只读认证服务组合，并有真实 `deepseek-flash` 结构化回执；默认headless阻断骨架仍不启用。

两个服务均已通过原生workflow/health调用；隔离Study保存v1/回读、History原文与摘要、Memory pending历史候选均已验证。原收件目录和MCP bundle转换问题已有实际修复及回执；没有扩大生产权限。模型、隔离SDK、当前Host和生产验收继续分开判断，最终发行仍须完整Checks。历史注册调查见[Desktop修复](v0.9.0-desktop-mcp.md)，动态门槛见[发布要求](v0.9.0-release-gates.md)。

## Agent A / B 编排层（2026-10-24 历史检查点）

新增独立、显式接线的编排 SDK，统一任务、适配器注册/能力筛选、状态机、私有元数据审计、有限重试与候选指纹恢复，通过固定 Gateway 操作接入三域。隔离合成 SDK 与 MCP 测试不等于当前 Desktop 或生产验收；当前 Host 未注册 cognivault，DeepSeek Harness 的受限执行条件尚未确认，因此整体为 **PARTIAL**。详见 [使用与限制](agent-orchestration.md) 和 [交付检查点](checkpoints/2026-10-24-agent-orchestration.md)。正式版本仍为 v0.8.0，历史验收范围不变。

## Memory 与三域学习协同（开发源码，未正式发布）

已实现持久化 Memory、版本/失效/幂等请求、History 候选审阅、Agent 提议的语义关系，以及带原始来源的 Study/History/Memory 聚焦检索。修订和失效必须携带读取时的 `target_guard`；未审核候选不进入学习建议。用法见 [Memory](memory-mvp.md) 和 [语义审阅](memory-semantic-review.md)。

2026-10-11，新版隔离数据完成真实 Codex Desktop 的六项既定验收：主动三域、普通问题零工具、注入防护、精确目标修订/原样重试、reader 发现隔离、实际应用重启后的持久性。reader 写工具不可发现，没有 Desktop 执行层拒绝回执；执行拒绝由独立 SDK 测试验证，不能混淆。修订后的学习回答实际引用当前 v2、历史消息、教材页和错题。此结果属于真实 Host 上的合成数据验收，不是生产学习效果或记忆真实性证明。

代码提交 `8f8ddb4` 的精确 CI 已通过：Windows 1062 passed / 10 skipped，Ubuntu 1052 passed / 20 skipped，stdio smoke 4 passed；两平台构建、包隐私审计与干净安装通过。随后文档提交与整合结果须以各自的 GitHub 检查为准。完整边界和证据见 [Desktop 收尾检查点](checkpoints/2026-10-11-desktop-mcp-closeout.md)。正式版本与历史生产计数保持其原始范围。

任意措辞的自动语义检测、独立当前事实验证仍为 PARTIAL；真实私人数据、真实 QMD 与生产备份恢复本轮 NOT RUN。来源存在、审阅确认和存储成功均不等于内容已独立验证。

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

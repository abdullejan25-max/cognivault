# Architecture

CogniVault 是一个 local-first、Agent-agnostic 的学习与记忆数据层。Agent Host 负责理解语言、推理和决策；Gateway 负责访问已配置的数据，并执行有类型、受权限控制的操作。

## System flow

```text
User
  → Agent Host
  → MCP transport（stdio）
  → Gateway
  → Services and adapters
  → 明确配置的本机数据
```

**Agent thinks; Gateway executes.** Gateway 不包含第二个 LLM，不推断用户意图，也不开放任意 Shell 或 SQL 访问。

## v0.9.0 候选编排层

可选编排 SDK 位于 Host 应用侧：Agent A 构造可信任务/依赖计划，按能力、授权和可信成本/延迟估计选择 Agent B；B 只能返回候选，不能选择 Gateway 工具、权限或路径。Engine 校验任务/关联/版本/来源，调用固定 domain handler，经 Gateway 提交并独立回读。Memory 提议始终待审；History 模型摘要不替换原始来源或确定性规范化。

受限 Harness 只组合模型与只读认证服务，独立 cwd/home，无工具、用户 profile、会话或指令加载；Windows 进程在执行前加入单进程 Job，POSIX 使用独立进程组。标准 HTTPS 接口没有文件/工具执行能力。两者均需显式外传授权，不能从材料正文取得授权。任务日志只记录标识、阶段、计数和摘要，正式数据仍使用原模型与 Gateway。

真实执行器、隔离 SDK Gateway、当前 Desktop MCP 和生产验收是独立证据范围。当前候选未满足全部发布门槛，见 [真实链路结果](v0.9.0-three-domain-acceptance.md) 和 [发布清单](v0.9.0-release-gates.md)。

## Component responsibilities

- **Agent Host** 理解请求并选择工具。Host memory 或 session metadata 不作为 Study、History 或 Wrong Answers 的权威存储。
- **MCP transport** 将协议 schema 与错误映射到 Gateway 调用。当前 transport 为本机 stdio MCP，不包含领域业务逻辑。
- **Gateway** 校验有类型的输入，执行 capability（能力权限）与路径边界检查，调用领域服务，统一结果并记录操作结果。
- **Services and adapters** 通过可替换的 backend contract（后端接口），例如 `StudyBackend` 和 `HistoryBackend`，实现领域规则。
- **Local stores and tools** 由私有配置显式指定。QMD、Tesseract 等外部工具由用户自行安装。

核心 contract 和 service 与 MCP SDK 分离。新 transport 可以复用同一 Gateway，但必须保持相同的接口、授权检查和数据边界。

错题工作流只有一个规范正文，位于 `src/cognivault/workflows/wrong_answer.md`，随 package 发布并通过 `study-workflow://wrong-answer` 暴露。各 Host 引用这份正文，不自行维护分叉版本。当前 stdio module 是 `cognivault.transports.mcp_stdio`，标准 Host registration 是 `cognivault`。

## Data domains and authority

- **Study：**配置的 StudyVault 是 Study 的唯一权威来源。QMD 对选定 collection 提供检索能力，不替代原始资料。
- **Personal History：**原始来源证据与 canonical conversations/messages 分开保存。Canonical History 是派生数据；只有来源边界、角色和顺序足以确定时才生成规范化结果。含糊或不支持的输入保留为 source-only。
- **Wrong Answers：**题目来源及其证据与 Agent 提供的分析分开保存。分析按版本追加并关联到来源，新版本不会覆盖旧版本。
- **Assets and Documents：**显式登记原始字节。提取的页面文本和 OCR 是派生表示，并保留到来源 Asset 的链接。
- **Legacy / Source Evidence：**导入记录保留来源类型和 provenance（数据来源与处理记录）。语义不明的历史输入不会被伪造为 canonical messages。
- **Projection and recovery copies：**projection（投影视图）和 isolated restore 副本用于展示或验证；权威数据仍由原始指定数据源提供。

## Provenance and normalization

持久记录保留来源引用、origin 和 append-only write provenance。Source occurrence time 与本机导入时间分别记录。版本化分析保留 supersession links。可选 caller / Agent identity 标为 `reported / unverified`，该字段本身不构成身份认证。

History normalization 是确定性处理，不使用 LLM。它保留原始证据，不猜测缺失信息，并允许不支持规范化的来源继续保持 source-only。具体约定见 [source ingestion](history-source-ingestion.md) 与 [History normalization](history-normalization.md)。

History acquisition ledger 的新 `imported` / `reused` outcome evidence 使用 `authority = "cognivault"`。已存的 `study_system` authority 是弃用的历史 provenance，仍可读取与校验，保持原 payload；名称变更不迁移或改写 schema、hash、source identity 或任何已存 evidence。ledger 不是当前 Gateway 状态的证明。

## Capability and local-data boundary

Capability 由本机配置显式授予：`read`、`ingest`、`write`、`projection` 和 `admin`。省略 permission 配置时默认只有 `read`。MCP discovery 会隐藏当前权限不可用的操作；Gateway 方法也会再次检查同一边界。

私有数据根目录和 backend 位置必须明确配置。系统不会自动扫描个人目录或上传 StudyVault 数据。路径校验会拒绝不安全组件，包括 symlink 和 Windows reparse point。公开错误码与消息采用固定、安全的词汇，不暴露 backend 异常详情。

读取、计数、存在性判断和写入都经过同一 Gateway 边界。Host adapter 与离线准备工具不得绕过 Gateway 直接访问 V2 store。

## Projection and recovery

批量 projection 由单独的 `projection` capability 控制。Renderer 生成确定性结果；Writer 仅修改 manifest 管理的文件，并保留所有权范围外的内容。Projection 不替代 Study 或来源证据。

Recovery 是通过 Gateway 执行的显式管理操作。Restore 在隔离 target 中通过 manifest、logical digests、provenance 和领域读取回查来验证。该副本用于证明可恢复性，不承担第二份在线权威数据。操作边界见 [private recovery](p13-recovery.md)。

## Extension points

- Backend 可在领域 contract 后替换，数据权威仍由 Gateway 边界定义。
- 新 transport 可调用现有 Gateway，同时保留既有接口和 capability 检查。
- 新的确定性 source adapter 可以保留未知结构，不放宽 canonical normalization 规则。
- 新 projection 可基于受限 Gateway snapshot 增加视图，不接管 primary data。

当前 release、Host、acquisition 和 recovery 验收情况见 [Current State](current-state.md)。架构决策记录在 [ADRs](adr/) 中。

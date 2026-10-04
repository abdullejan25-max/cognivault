# ADR-007: Agent-Only Intelligence and Runtime Dependency Policy

状态：Accepted
日期：2026-09-25

## Context

后续 History、文档和错题功能会增加数据处理，但不能把 Gateway 演变成第二个教学或规划 Agent。Phase 3 的 QMD 检索行为需要保留。

## Decision

- Agent host 独自负责理解用户意图、教学、分析与工具选择。Gateway 只执行显式、确定性的 typed operations。
- V2 Python 运行时不直接依赖模型提供商客户端或推理框架，不调用 LLM/VLM，也不保存模型凭据。QMD 是既有的外部检索 adapter；其索引和搜索实现不授予 Gateway 教学或规划职责。
- 运行时依赖策略在测试中检查 `src/cognivault/**/*.py` 的 Python 语法树（改名前路径为 `src/chatgpt_study_system/**/*.py`），以及 `pyproject.toml` 中 `[project].dependencies` 的必需运行时依赖。扫描按模型/推理命名空间特征和客户端符号识别常见 SDK，并用合成违规代码验证常规 import、字面量动态 import 与客户端构造。它不扫描测试、文档、可选开发依赖或整个锁文件；一般 HTTP 客户端不视为模型依赖。
- Gateway 的公开错误使用固定 code 与安全消息。新领域预留 `HISTORY_UNAVAILABLE`、`RESOURCE_NOT_FOUND`、`CONFLICT`、`PERMISSION_DENIED`、`PAYLOAD_TOO_LARGE`、`STORAGE_UNAVAILABLE`、`UNSUPPORTED_MEDIA_TYPE`；仅实际操作落地时才定义触发条件。

## Consequences

- History/文档/错题 adapter 可以做校验、索引、解析及持久化，不能自行生成教学判断或调用模型。
- 匹配上述命名特征的直接模型 SDK 或推理依赖会触发运行时边界测试失败。静态扫描无法证明不存在改名、间接引入或运行时拼接的模型调用；新增依赖仍需人工审查。
- 远程 transport/Tunnel 不属于 Core；当前无远程连接实现。

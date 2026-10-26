# Agent A / B 通用编排设计

本任务按用户附件授权自主设计、实施和审查，不设置逐步批准。起点为干净的 `a8f12d5`，与 2026-10-24 获取的 origin/main 一致；在现有隔离工作树、`codex/agent-orchestration` 分支开发。

## 选择与边界

采用独立 Python 应用编排层，复用 Gateway、Memory 候选审阅、History 规范化、Study 原始材料和现有幂等机制。另两个方案：在 Gateway 内执行模型会扩大可信边界；引入第三方工作流引擎会增加依赖和运维，本阶段均不采用。不更改业务 schema，不新增业务数据库，不默认启用任何模型或生产权限。

Agent A 接受可信应用提供的 Task（任务标识、关联标识、类型、输入引用、授权范围、超时、有限重试、外传授权）。Agent B 接口仅接收按任务裁剪的材料，返回 Result（绑定标识和版本、结构化候选、置信度、来源、不确定性），无 Gateway、shell 或配置访问权。描述符区分已声明能力与当前调用验证能力、合成与真实执行。外部适配器在健康检查和材料交付前均检查外传授权。

固定 domain handler 准备材料、校验候选、执行指定 Gateway 操作、独立回读。模型不能提供工具名、权限或任意路径。GatewayPort 支持已连接 MCP session 和隔离测试用 Gateway；不在生产 Host 不可用时启动替代客户端。

## 生命周期与恢复

RECEIVED → PLANNED → AUTHORIZED → RUNNING → VALIDATING → COMMITTING → VERIFYING → COMPLETED。低置信度、缺证据、不确定内容和正式 Memory 审阅进入 WAITING_FOR_REVIEW；错误分类为 RETRYABLE_FAILURE、FAILED、CANCELLED、PARTIAL。写入开始后取消/超时必须保留不确定状态，不能报告干净失败。有限重试仅适用于 Agent 的无业务副作用阶段。

任务日志只保存请求/候选指纹、阶段、计数、适配器身份和脱敏错误码，不保存材料、模型正文或 Gateway 原文。任务文件原子替换并 fsync；操作系统文件锁提供同任务跨实例互斥，崩溃释放锁。相同 task_id 的不同输入或权限拒绝为冲突。稳定幂等键由 task_id、请求指纹与操作类型生成。

COMMITTING 之前记录候选指纹。写入中断后必须由可信调用方重新提交原候选，匹配指纹后才重放具备业务幂等性的固定操作并回读；禁止重新生成候选后盲写。日志损坏应拒绝恢复，不能当新任务。完成任务不重复执行。不同 task_id 是不同业务授权，跨任务去重由各域现有机制负责。

## 三域

Study 以已有材料/错题 source_id 为锚，保存带来源的分析并按返回版本回读；图片注册和题目来源保留 Gateway 原入口。History 摘要始终为派生结果；原始导入只消费可信调用方指定的已校验 manifest，复用正式来源导入，不把模型输出变成原始对话。Memory 只提议待审候选，使用原文引用、冲突检查和现有审核机制，绝不自动提升推断为长期事实。联合检索沿用现有三域 evidence bundle，保留无结果与部分不可用状态。

## 验收与威胁

先写失败测试，再实现。覆盖三域真实 Gateway 的合成读写回读、候选隔离、越权拒绝、结构/身份/来源不匹配、注入、NaN 置信度、超时、取消、有限重试、同任务并发、崩溃与幂等、元数据日志隐私。执行完整 pytest、编译、lockfile、包构建/审计/干净安装及已有 Windows/Ubuntu CI。

生产验收、真实外部模型和合成 SDK 证据分列。当前 Host 对已知 workflow 返回 unknown MCP server，不得以合成 Gateway 代替生产证据。DeepSeek H 身份、调用协议和外传范围依调查决定；无法验证则 BLOCKED。交付文档必须记录实际通过/跳过/失败和续跑方式。

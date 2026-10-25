# 变更记录

## 0.9.0 — 候选开发源码（未正式发布）

状态：**PARTIAL**。`pyproject.toml` 和 `uv.lock` 的 `0.9.0` 表示当前源码及候选包版本，不表示已创建 Git tag、GitHub Release 或发布到包索引。

- 引入 Agent 编排内核、任务领域映射、有限重试、授权筛选、候选校验与 Gateway 回读；Agent 生成候选，Gateway 执行持久化。
- 新增仅模型/只读认证组件的 Harness 执行器、固定 HTTPS 无工具接口、可信依赖计划与结构化错误。真实 `deepseek-flash` 已完成 Study 保存/回读与 History 摘要/原文回读；真实 Memory 响应和检索尚未通过，Desktop 原生 MCP 仍为 BLOCKED，不能用 synthetic 测试替代。
- 修复取消后启动写入、认证阻塞耗尽、停用恢复、写后回读拒绝、计划丢失 PARTIAL、Gateway 多行显示规范化回读、父进程提前退出遗漏后代与启动取消丢失句柄；Windows Job 限单进程、POSIX 进程组清理，并防止 HTTPS 环境 CA 覆盖。
- 声明执行器所需的直接依赖 `httpx>=0.28,<1`；保留 canonical 包、`study-migrate` 和 deprecated stdio 启动 shim。
- 包审计从 `pyproject.toml` 读取版本，要求当前版本 wheel 与源码归档同时存在，继续检查私有路径和配置泄漏。

候选说明见 [v0.9.0](docs/releases/v0.9.0.md)，发布条件见 [发布门槛](docs/v0.9.0-release-gates.md)。历史版本以各自 [release 文档](docs/releases/v0.8.0.md) 为准。

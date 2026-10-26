# 变更记录

## 0.9.0 — 候选开发源码（未正式发布）

状态：**PARTIAL**。`pyproject.toml` 和 `uv.lock` 的 `0.9.0` 表示当前源码及候选包版本，不表示已创建 Git tag、GitHub Release 或发布到包索引。

- 引入 Agent 编排内核、任务领域映射、有限重试、授权筛选、候选校验与 Gateway 回读；Agent 生成候选，Gateway 执行持久化。
- 新增仅模型/只读认证组件的 Harness 执行器、固定 HTTPS 无工具接口、可信依赖计划与结构化错误。真实 `deepseek-flash` 已完成隔离 SDK Study/History/检索；10 月 26 日 Study 又完成当前 Desktop 原生工具保存与独立回读。修复 MCP 错题包固定包装转换并校验操作返回形状；Memory 保留原发生时间和未知状态。真实 Memory 仍未完成，原生 History 收件配置待 Host 重载，不能用 synthetic 测试替代。
- 待审回执保留固定原因、数值、计数与候选摘要，避免重复请求仅为诊断；不保存正文、不改变门槛，兼容旧任务日志。
- 修复取消后启动写入、认证阻塞耗尽、停用恢复、写后回读拒绝、计划丢失 PARTIAL、Gateway 多行显示规范化回读、父进程提前退出遗漏后代与启动取消丢失句柄；Windows Job 限单进程、POSIX 进程组清理，并防止 HTTPS 环境 CA 覆盖。
- 声明执行器所需的直接依赖 `httpx>=0.28,<1`；保留 canonical 包、`study-migrate` 和 deprecated stdio 启动 shim。
- 包审计从 `pyproject.toml` 读取版本，要求当前版本 wheel 与源码归档同时存在，继续检查私有路径和配置泄漏。

候选说明见 [v0.9.0](docs/releases/v0.9.0.md)，发布条件见 [发布门槛](docs/v0.9.0-release-gates.md)。历史版本以各自 [release 文档](docs/releases/v0.8.0.md) 为准。

# 变更记录

## 0.9.0 — Agent Orchestration

本节描述0.9.0源码；实际发行状态以对应Git tag与GitHub Release为准。`pyproject.toml` 和 `uv.lock` 的版本号本身不表示已经发布到包索引。

- 当前Desktop原生三域任务已分别完成真实模型调用及独立回读；Memory只保存pending历史候选，不自动审批为当前事实。新增可信可选proposal_scope绑定历史分类与reported主体标签，检索支持保留有界notes；实际不确定性仍按原门槛待审。[原始回执与失败轨迹](docs/checkpoints/2026-10-26-v090-native-three-domain-closeout.md)分别记录。

- 新增仅管理员可发现和执行的 `initialize_history` 首次部署入口；严格拒绝既有主库和辅助文件。History/Memory 首次初始化改为内存建库、仅通过独占持有的文件句柄写入，修复 journal 并发替换时外来文件可能被 SQLite 删除的竞态；不迁移已有库、不扩大生产权限。

- 引入 Agent 编排内核、任务领域映射、有限重试、授权筛选、候选校验与 Gateway 回读；Agent 生成候选，Gateway 执行持久化。
- 新增仅模型/只读认证组件的 Harness 执行器、固定 HTTPS 无工具接口、可信依赖计划与结构化错误。真实 `deepseek-flash` 已完成隔离 SDK 及当前原生任务。修复 MCP 错题包固定包装转换并校验操作返回形状；Memory 保留原发生时间和未知状态，不能用 synthetic 测试替代真实证据。
- 待审回执保留固定原因、数值、计数与候选摘要，避免重复请求仅为诊断；不保存正文、不改变门槛，兼容旧任务日志。
- 修复取消后启动写入、认证阻塞耗尽、停用恢复、写后回读拒绝、计划丢失 PARTIAL、Gateway 多行显示规范化回读、父进程提前退出遗漏后代与启动取消丢失句柄；Windows Job 限单进程、POSIX 进程组清理，并防止 HTTPS 环境 CA 覆盖。
- 声明执行器所需的直接依赖 `httpx>=0.28,<1`；保留 canonical 包、`study-migrate` 和 deprecated stdio 启动 shim。
- 包审计从 `pyproject.toml` 读取版本，要求当前版本 wheel 与源码归档同时存在，继续检查私有路径和配置泄漏。

候选说明见 [v0.9.0](docs/releases/v0.9.0.md)，发布条件见 [发布门槛](docs/v0.9.0-release-gates.md)。历史版本以各自 [release 文档](docs/releases/v0.8.0.md) 为准。

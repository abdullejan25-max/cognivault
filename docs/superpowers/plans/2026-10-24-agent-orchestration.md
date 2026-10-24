# Agent 编排层实施计划

> **For agentic workers:** Use executing-plans for the core, scoped agent work for domain integration and independent review. User explicitly authorizes continuous execution.

**Goal:** 构建复用 Gateway 的 Study / History / Memory 通用、可恢复、可审计编排层。

**Architecture:** Task / Result 与适配器协议独立于模型。操作系统锁与原子 JSON 元数据日志保护任务；固定 domain handler 经 GatewayPort 执行并回读。

**Tech Stack:** Python >=3.11、标准库 asyncio/dataclasses/json、现有 MCP、pytest。无新增运行时依赖。

## Global Constraints

- 正式数据读写 Gateway-only；只使用 tmp_path 合成数据自动测试。
- 不改生产配置、不记录凭据、不访问微信、不默认外传材料。
- 合成、SDK、Desktop Host、真实模型证据分别报告。
- 实施位置 src/cognivault/orchestration；不重写稳定业务模块。

### Task 1: 真实环境调查与基线

- [x] 核对 worktree、HEAD、origin/main、权限规则与现有 Gateway 接口。
- [x] 记录 DeepSeek Harness 安装、调用入口及限制；区分配置/连接/调用。
- [x] `uv run --locked --extra dev pytest -q` 建立基线。

### Task 2: 协议、状态、审计与恢复

Files: models.py、journal.py、engine.py、adapters.py、__init__.py；tests/test_orchestration.py。

- [x] 写 Task/Result、输出绑定、正常执行及不可信结果失败测试，运行确认缺模块失败。
- [x] 实现协议、Registry、合成适配器，字段严格限制，类型与有限数值校验。
- [x] 实现 journal 同任务 OS 锁、请求冲突检测、原子保存与坏文件 fail-closed。
- [x] 实现 run/resume、有限重试、取消、超时、回读及部分成功判定。
- [x] 覆盖崩溃写入窗口、跨实例冲突、外传策略和日志不含正文。

### Task 3: 三域固定 Gateway 映射

Files: domains.py；tests/test_orchestration_domains.py。

- [x] 合成真实 Gateway 测试先行，验证 Study 保存后独立回读。
- [x] History 摘要与原文隔离，导入使用可信 manifest 与现有幂等机制。
- [x] Memory 原文候选、冲突检查与回读，保留 pending review。
- [x] 已连接 MCP port 校验正式工具结果；不提供工具名/路径自由执行入口。

### Task 4: 独立审查、兼容与交付

- [x] 独立 review 权限、注入、状态收敛、恢复、隐私，修复并回归。
- [x] 完整 pytest、compileall、uv lock --check、uv build、verify_package_contents。
- [x] 干净环境安装 wheel，验证新模块导入及 legacy shim。
- [x] 文档记录用法、三域验收、真实接入、安全结论及 BLOCKED 续跑。
- [x] 检查 diff 与提交内容，提交、push、创建草稿 PR #22，实现提交 Windows / Ubuntu CI 通过；真实接入和当前 Desktop 验收未完成，保持草稿，不合并/发布。

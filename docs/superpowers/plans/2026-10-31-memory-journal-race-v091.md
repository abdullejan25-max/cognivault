# Memory journal race v0.9.1 Implementation Plan

> **For agentic workers:** 按本计划逐项实施，使用 systematic-debugging、test-driven-development、verification-before-completion；用 requesting-code-review 完成独立只读审查。

**Goal:** 修复正常并发下的 Memory 路径误报，并在发行检查通过后发布 v0.9.1。

**Architecture:** 父路径沿用现有 reparse 检查；叶子文件通过一次 `lstat` 返回的快照判断类型、reparse 属性和链接数。只接受文件不存在，其他 I/O 错误拒绝。

**Tech Stack:** Python 3.11+、pytest、SQLite、uv、GitHub Actions。

## Global Constraints

不访问生产数据，不修改 schema、权限、WAL 保护和事务规则；保留首次失败，不混用 SDK / 当前 Host / 生产证据。沿用当前隔离 worktree，分支 `codex/fix-memory-journal-race-v091`。

## Task 1: 修复与发行

**Files:** 修改 `src/cognivault/adapters/memory.py`；新增 `tests/test_memory_path_validation.py`；修改 `pyproject.toml`、`uv.lock`、当前版本断言；新增 `docs/releases/v0.9.1.md` 并更新当前文档入口及检查点。

**Interfaces:** `SQLiteMemoryStore._validate_path(self)` 仍无返回值，异常继续为 `GatewayError('STORAGE_UNAVAILABLE', ...)`；所有公开调用保持原接口。

- [x] 先添加确定性 journal 删除回归及安全拒绝测试。使用真实临时文件，并在叶子元数据读取前/后删除 journal；写入及回读应成功。
- [x] 执行 `uv run --locked --extra dev pytest -q tests/test_memory_path_validation.py`；确认旧实现的读取后消失案例以 `STORAGE_UNAVAILABLE` 失败。
- [x] 将多次叶子文件读取替换为：`try: info = candidate.lstat()`；`except FileNotFoundError: continue`；通过 `stat.S_ISREG(info.st_mode)`、`info.st_nlink` 和 `getattr(info, 'st_file_attributes', 0) & 0x400` 检查，不吞掉其他错误。
- [x] 运行所有 `test_memory*.py`，再重复原始并发用例；记录每轮结果，不把重试通过覆盖失败。
- [x] 将源码版本与两个当前版本测试断言更新为 `0.9.1`，执行 `uv lock --offline`，确认依赖版本不变；执行版本测试。
- [x] 运行本地全量 `uv run --locked --extra dev pytest -q -rs`，构建/审计 wheel 和 sdist；干净安装后验证 canonical / legacy stdio。
- [x] 由独立 reviewer 只读检查未提交 diff，修复阻断问题；更新测试检查点及发行说明。
- [ ] 检查公开 diff 无私有数据；提交并推送分支，创建 PR。精确提交 Windows / Ubuntu CI 全通过后合并，再检查合并提交 CI。
- [ ] 以已验证合并提交创建 v0.9.1 tag / GitHub Release，附带经审计包及校验和；核对远端发行回执与 tag 指向。

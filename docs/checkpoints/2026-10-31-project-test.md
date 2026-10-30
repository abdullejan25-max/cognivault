# 2026-10-31 项目测试报告

本次针对提交 `d730bcfb4562ae6dee8b7a49a8daf41f2cbb533a` 执行本地测试。开始时工作区干净，处于 detached HEAD，包版本为 `0.9.0`。本报告记录本次运行，不复用历史检查点作为通过证据。

后续修复另见 [v0.9.1 修复检查点](2026-10-31-memory-journal-race-fix.md)。以下首次失败和未修改实现的描述保留其原始运行范围。

## 当前结论

全量测试执行完成，结果为 **FAIL：1529 passed、1 failed、17 skipped**，耗时 **738.27 秒（12 分 18 秒）**。唯一失败是已复现的 Memory 并发问题。补充的隔离 QMD 集成、可选 PDF 页面渲染、包构建、包隐私审计、干净安装与两种 stdio 启动入口检查通过。当前 Desktop 原生 Gateway 验收受阻，不能认定整个项目已经通过验收。

## 环境与边界

- Windows，PowerShell 7，Python `3.11.15`，uv `0.12.3`。
- 锁定测试环境：pytest `9.1.1`、MCP SDK `1.30.0`、pypdf `6.19.0`、httpx `0.28.1`。
- QMD `2.8.3`、Node `v24.19.0`，显式选择已安装的程序，在新建临时目录创建合成资料和派生索引。
- 没有读取、写入或迁移真实学习资料、聊天记录及生产数据库；没有调用真实外部模型。
- 未修改业务代码、测试断言、保护阈值或本地 MCP 配置；没有提交、推送或发布。
- 原始日志、JUnit XML、检索 JSON 与诊断脚本保存在本地临时报告目录，不纳入 Git。

## 检查矩阵

| 检查 | 结果 | 本次证据 |
| --- | --- | --- |
| 锁文件 | PASS | `uv lock --check`，退出码 0 |
| 开发依赖 | PASS | `uv sync --locked --extra dev`，退出码 0 |
| 全量自动化测试 | FAIL | `uv run --locked --extra dev pytest -q -rs --junitxml=<本地报告目录>/pytest.xml`；1529 passed、1 failed、17 skipped，退出码 1 |
| 真实 QMD 程序、合成资料集成 | PASS | 3 passed，56.19 秒 |
| 可选 PDF 原始页面渲染 / MCP 二进制资源 | PASS | 独立 `--extra dev --extra pdf-ocr` 环境，1 passed，16.70 秒 |
| 固定检索评估 | DONE，有已知缺口 | 32 案例，26 项质量可评估、5 项不支持、1 项容量保护契约通过 |
| wheel / sdist | PASS | `uv build --wheel --sdist`，退出码 0 |
| 包内容隐私审计 | PASS | wheel 90 entries，sdist 350 entries |
| 干净安装 | PASS | 独立 Python 3.11 环境安装 wheel，版本及 Harness JS 资源断言通过 |
| 已安装包的 canonical / legacy stdio | PASS | 两入口均完成 initialize、身份、health、两个 workflow 读取和只读工具发现 |
| 语法检查 | PASS | Python `compileall`、JS `node --check`、PowerShell parser |
| 依赖兼容性 | PASS | 测试环境及干净安装环境的 `uv pip check` |
| 当前 Desktop 原生 MCP | BLOCKED | 直接读取已知 workflow 返回 `unknown MCP server 'cognivault'` |
| 生产业务与真实模型端到端 | NOT RUN | 不用合成 SDK / stdio 结果替代生产或当前 Host 证据 |
| Ubuntu / 远端 CI | NOT RUN | 本次仅执行本机 Windows 检查 |

干净安装遵循 CI 中安装 wheel 的方式，按依赖范围重新解析依赖，并非锁定环境的重复验证；其中 MCP SDK 为 `1.30.0`，pypdf 为 `6.21.4`。两个环境的验证结果分别保留。

## Memory 并发失败

失败测试为 `tests/test_memory_acceptance.py::test_concurrent_same_fact_and_revision_conflict`。测试使用 4 个线程并发创建同一事实，要求 8 个请求复用同一 Memory，后续并发修订仅一个成功，其余返回 `CONFLICT`。

已确认的现象：

- 全量运行在 `src/cognivault/adapters/memory.py:521` 的 `OSError` 处理分支失败；单项首次复跑通过，后续再次单项复跑也通过。
- 随后进行 5 次独立 pytest 进程复跑：第 1、2、3 次通过，第 4、5 次失败。因此不能用一次复跑通过覆盖全量失败。
- 两次失败均发生在创建阶段，调用链为 `create` → `_connect` → `_validate_path`，在 `src/cognivault/adapters/memory.py:517` 抛出 `STORAGE_UNAVAILABLE`。
- 另用临时诊断包装器运行 6 次原测试函数，得到 5 次通过、1 次失败；失败捕获于路径校验的 `OSError` 处理分支，当前候选为 `memory.db-journal`，捕获后文件已不存在。

判断：现有证据支持 SQLite journal 在并发创建/删除期间与文件路径检查发生竞态。具体检查包含多次 `exists` / `is_file` / `stat`，检查期间文件状态可能变化。该判断基于本次日志与诊断，尚未通过修复后的回归验证；不推断发生数据损坏。

建议后续针对 journal 生命周期建立确定性竞态回归，再评估在保留 reparse point、硬链接及路径替换保护条件下如何处理正常并发。此次只记录问题，没有修改实现。

## 检索评估

命令为 `uv run --locked --extra dev python scripts/evaluate_retrieval.py --run --qmd-node <已安装Node> --qmd-cli <已安装QMD的JS入口>`。

- fixture：`p15-synthetic-1`，32 案例。
- fixture SHA-256：`07ebc25e54c2003c24913fa5dbe8603d1a8b85a3fb681c7e65be6d7b82a4e8ad`。
- 宏平均 Recall@10（前十条结果的相关资料召回率）：`0.95`。
- 宏平均 MRR@10（首个相关结果排名倒数的平均值）：`0.9083333333333333`。
- `study-02` 的中文问题“为什么分母不能直接相加”未命中预期 `study:equal-parts.md`，Recall@10 为 0。这是本次合成评估记录的检索缺口，不能概括为所有中文查询均失效。
- 不支持 5 项：Study 的 `page_range` / `chapter_ids`，Wrong Answer 的 `time_range`，History 的 `time_range` / `source_ids`。
- `documents-05` 返回 `PAYLOAD_TOO_LARGE`，符合该容量保护案例的预期，`contract_pass=true`。报告原始 `errors=1` 包含此项，不能把它解释为意外执行故障，也不能从原始计数中删掉。

以上只证明固定合成资料下的行为，不证明真实个人资料上的学习效果。本次同时运行其他测试，且没有清空操作系统缓存，不把检索延迟当作独占环境的性能基准。

## 未覆盖部分

当前聊天的工具注册表没有 `cognivault`，本工作区也不存在 `.codex/config.toml`；直接读取 `study-workflow://wrong-answer` 返回未知服务器。当前项目路径已确认，但没有获得项目受信任并加载本地配置的证据。未改动信任设置或改用脚本访问生产 Gateway。

干净包的 stdio 测试是独立 MCP SDK 驱动的合成检查，不是当前 Desktop 原生 Host 验收。真实资料 QMD、真实 Codex CLI Host、真实模型、生产读写、Ubuntu 与远端 CI 均没有本次通过证据。代码行覆盖率未测量；全量指仓库测试集合，不代表所有运行场景。

## 全量最终结果

JUnit 与控制台汇总一致：共 1547 项，1529 passed、1 failed、17 skipped，0 errors。唯一失败为上述 Memory 并发测试。pytest 控制台耗时 738.27 秒，JUnit suite 时间为 737.989 秒；两者计时范围略有差别，均保留原始值。

17 项跳过分类如下，后续补测独立记录，不合并成一轮“全量通过”：

| 原因 | 数量 | 处理 |
| --- | --- | --- |
| 当前 Windows 环境不能创建测试所需 symlink / junction | 7 | NOT RUN；不更改系统权限或跳过条件 |
| POSIX 权限 2 项、process group 1 项、FIFO 1 项 | 4 | NOT RUN；需要兼容的 POSIX 环境 |
| 真实 Codex CLI Host 显式开关未设置 | 1 | NOT RUN；不等于当前 Desktop Host |
| 真实资料 QMD 显式开关未设置 | 1 | NOT RUN；不启用直接接触真实资料的测试 |
| 合成资料上的真实 QMD 程序未显式指定 | 3 | 已在补充运行中全部通过：3 passed，56.19 秒 |
| 可选 PyMuPDF 依赖未安装 | 1 | 已在独立 `--extra dev --extra pdf-ocr` 环境补测通过：1 passed，16.70 秒 |

本次不具备“全部测试通过”或“可发布”的结论。优先后续处理 Memory 并发路径检查问题，并保留首次失败及每次复跑结果。

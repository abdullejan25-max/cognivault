# P14 候选复用与执行边界

执行核实日期：2026-10-07。基线 main / remote main：`c4e8b63e0425c7c89e9e03ed3f104ae36e27edbc`。正式 tag target：`748ef70e7ac29bde3ce6c01d04333ac0ee9e5526`。

本轮沿用现有隔离 worktree，在 `codex/p14-p15-baseline` 开发。起始 detached HEAD；仅有本聊天生成的两份预审文档，已单独提交为 `a6e8c8c`，没有覆盖用户代码。公开代码与 Git 对象调查不访问真实资料。

## 候选地图

候选分支 `codex/v0.8-operational-reliability` 与 main 分叉；它使用旧主包、旧权限语义，未含当前 Windows installer。下列判断由预审和本轮精确 `git show` 复核得到，禁止整分支 merge 或 bulk cherry-pick。

| Slice | 当前实现 / 测试 / 文档 | 分类 | 复用决定 |
|---|---|---|---|
| `d2186a9` 页码修复 | Document/MCP 999 已存在；wrong-answer adapter 仍 64；当前 tests 未抓住 65 合同冲突 | candidate_only | 复用 `MAX_PAGES` 思路，在 canonical adapter 重写窄修复并先运行失败回归。候选另改 projection，需独立核实，不能无证据照搬 |
| `803a953` / `b375d86` control plane / TOML merge | main 已有 Gateway capability checks、受控 setup 和未知配置拒绝/保全；统一 binding/status 缺失 | main_present + candidate_only | 保持 main admin 语义，不移植旧权限模型；先修已证实 discovery 漂移，广泛 binding 不在首批 |
| `3075048` ledger / resume package | main 已有 journals、receipts、snapshot manifest/catalog、局部 replay；通用持久任务 status 缺失 | main_present + candidate_only | 只研究 `recovery_snapshot` status/reconciliation；不引入候选 `operations.sqlite3` 或通用 runtime；候选缺 handler 不能声称全面 resume |
| `27253cc` retrieval evaluator | main 没有检索质量 benchmark；已有 QMD smoke、Document/History/WA retrieval 单测 | candidate_only | 复用测量思想，不复制旧模块。独立编写 32-case gold；修 top-k、漏检分母、ID 粒度、cold/warm 对比；新 runner 通过合成 Gateway |
| `db03e6d` batch/lifecycle | main 已有正式 wrong-answer workflow、immutable source、版本、幂等、冲突、provenance、bundle readback | main_present + candidate_only | 只补正式 workflow 的保存后回读；不增加 attempt/lifecycle 引擎 |
| Windows/Ubuntu/install/package | main 已有 installer、canonical 包、兼容 shim、CI、wheel/sdist/privacy audit | main_present | 扩充与此次 diff 有关的证据，避免重复 service/installer |

`main_present` 仅指具体已有能力；混合行分开解释已有与候选，不能把旧候选视为当前发布。

## 文件级接续证据

以下路径均相对当前 repository。`absent` 指当前 main 尚无对应文件/能力，候选路径只可通过表内 commit 的 `git show` 查看。公开预审的 [当前验证结果](2026-10-07-post-release-architecture-preaudit.md#L103)、[候选与主线边界](2026-10-07-post-release-architecture-preaudit.md#L123)、[复现问题](2026-10-07-post-release-architecture-preaudit.md#L242)、[旧评价器四项缺陷](2026-10-07-post-release-architecture-preaudit.md#L339)提供核验入口。

| Slice | 当前实现路径 | 当前测试路径 | 当前文档 / absent | 拟采用文件 |
|---|---|---|---|---|
| `d2186a9` | `src/cognivault/adapters/documents.py`、`adapters/wrong_answers.py`、`obsidian_projection.py`、`transports/mcp_stdio.py` | `tests/test_wrong_answers.py`、`test_wrong_answer_mcp.py`、`test_obsidian_projection.py` | 预审 §4 页码复现；65–999 正确登记回归 absent | 首批 `adapters/wrong_answers.py`、相关 wrong-answer tests；projection 后续独立回归，非盲移植 |
| `803a953/b375d86` | `.codex/setup_mcp.py`、`src/cognivault/gateway.py`、`runtime.py`、`transports/mcp_stdio.py`；`control_plane.py` absent | `tests/test_codex_project_bootstrap.py`、`test_phase8_permissions.py`、`test_canonical_gateway.py`、`test_mcp_stdio.py` | `docs/codex-host-setup.md`、`privacy-boundary.md`；统一 binding/status 文档 absent | 首批仅 `transports/mcp_stdio.py`、权限与 MCP tests；不采用通用 control plane |
| `3075048` | `src/cognivault/recovery/service.py`、`recovery/io.py`、`migration/history_ledger.py`；`operations/ledger.py` absent | `tests/test_recovery.py`、`test_recovery_supplement.py`、`test_history_ledger.py`；统一任务 tests absent | `docs/p13-recovery.md`、预审 §3 Recovery；有限 status/reconciliation 合同 absent | `docs/research/p16-finite-recovery-contract.md`、`tests/test_operation_recovery.py`；规格确定后才采用聚焦 status 模块与 Gateway/MCP 接口 |
| `27253cc` | `src/cognivault/adapters/study_qmd.py`、`documents.py`、`wrong_answers.py`、`canonical_history.py`；评价器 absent | `tests/test_qmd_runtime_smoke.py`、现有领域 tests；quality evaluator tests absent | 预审 §6 P15 设计；main baseline 报告 absent | `tests/fixtures/retrieval/manifest.json`、四域 `cases.json`、`scripts/evaluate_retrieval.py`、`tests/test_retrieval_evaluation.py`、`docs/research/p15-retrieval-baseline.md` |
| `db03e6d` | `src/cognivault/workflows/wrong_answer.md`、`gateway.py`、`adapters/wrong_answers.py`；batch/lifecycle adapter absent | `tests/test_wrong_answers.py`、`test_wrong_answer_mcp.py`、`test_projection_collector.py` | 正式 workflow 即唯一规则来源；预审 §3 Daily Workflows | `workflows/wrong_answer.md`、`tests/test_wrong_answer_mcp.py`，只补回读；其他候选不采用 |
| platform/package | `install.cmd`、`install.ps1`、`scripts/setup_local.py`、`pyproject.toml`、`.github/workflows/ci.yml` | `tests/test_windows_installer.py`、`test_project_metadata.py`、`test_mcp_identity_transition.py` | `README.md`、`docs/*-host-setup.md`；本分支最终精确 HEAD CI 尚无 | 优先执行现有 build/install/privacy scripts；仅覆盖证据要求新增的 test/CI slice |

首行路径写全，其余同单元格缩写沿用相同目录；没有指向真实数据路径。上述是待执行文件边界，是否最终修改由 regression 与 measured gap 决定。

## 执行规则与新指令优先级

- 采用 2026-10-07 总指令执行 Phase A；前一预审的“仅调查”停止点已完成，不再阻止获授权实现。
- 新总指令允许必要时重建 CogniVault 自身派生数据，但本轮没有生产连接或重建必要性；不执行任何真实数据删除/迁移。原始教材、图片、导出、用户文件和 ownership 未知文件受保护。
- 对任何疑似微信路径立即 `SKIP_WECHAT`，不打开确认、不扫描、不索引、不清理。
- 真 Study/History/Sources/Assets/Wrong Answers 的 reads/counts/writes 全部通过正式 Gateway；本聊天直读已知 workflow 得到 `unknown MCP server 'cognivault'`，没有私有配置和生成的 Host 配置，信任状态未知。native Host 与 production 保持 UNKNOWN。
- 无生产连接时只使用明确人工生成的合成临时 fixtures，不用 direct client、inline override 或假配置补 production PASS。
- 一个实现任务一个可审查 diff；共享 worktree 不并行派发多个实现者。routine worker 使用 GPT-6.1 Sol Medium；安全语义和最终独立 review 可用 Astra Low/Medium。
- 开工 C 盘空闲约 4.2 GiB，仅用于容量判断。复用预审外置锁定测试 venv，避免重复下载/复制；不用真实大备份腾空间。
- 不自动 merge、tag、release；checkpoint 保存实际 code/fixture/evidence，当前候选 CI 必须与精确 HEAD 对应。

## 接续

第一批：页码回归 → discovery 权限矩阵 → 窄修复与 targeted regression → 独立 review → P15 gold/evaluator。状态与证据入口见 [执行 checkpoint](../p14-p18-execution-checkpoint.md)。

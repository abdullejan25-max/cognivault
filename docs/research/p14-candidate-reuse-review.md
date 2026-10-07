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

# CogniVault 自主开发 checkpoint

更新时间：2026-10-07 16:55 Asia/Shanghai。执行依据：[三天计划](superpowers/plans/2026-10-07-three-day-autonomous-development.md)及用户 2026-10-07 自主开发总指令。

## Repository

- Repository：`cognivault`；当前受管隔离 worktree 以 `git rev-parse --show-toplevel` 获取，私有绝对路径不写入公开文档。
- Branch：`codex/p14-p15-baseline`。
- Base HEAD / remote main：`c4e8b63e0425c7c89e9e03ed3f104ae36e27edbc`。
- 起始文档 commit：`a6e8c8c`。后续 HEAD 使用 `git log -1 --format=%H` 核实，checkpoint 所在 commit 不自指。
- 起始用户改动：无未知改动；此前两份预审文档为同一聊天产物，已保留并单独提交。
- 开工容量：约 4.2 GiB；没有读/复制生产 stores。

## Task status

| 工作包 | 状态 | 证据 / 下一步 |
|---|---|---|
| P14 复用审查 | DONE | `4957a75`、`f5a843c`；文件级地图独立 spec/quality review 通过 |
| 页码合同 / discovery | DONE（窄范围） | `ae6e34f`；RED 4 failed / 8 passed → GREEN 12 passed → 相关回归 93 passed / 1 skipped；独立 review 通过 |
| 下游 projection 页码 | DONE | `ca510d2`；RED 65/999 失败，GREEN 29 passed；相关回归 67 passed / 1 skipped；独立 spec/quality review 通过 |
| P15 gold / evaluator | PARTIAL（等待独立 review） | gold `64a5f19`、实现至 `854e673`；32 cases / 4 域，23 passed 含 QMD 2.8.3 sentinel；尚未跑质量 baseline |
| P15 baseline | 尚未开始 | 不优化搜索；记录 raw rankings、分母、coverage、cold/warm、errors |
| P16 recovery | 尚未开始 | 先规格和故障 tests；只 status/reconciliation，未知 owner 不接管 |
| P17 retrieval | DEFERRED | 等 P15 实测决定，零范围泄漏或可重复排名收益 |
| P18 / Phase B | 尚未开始 | 已有 workflow 回读、工程巡检与精确 diff 验证 |
| Native Host | UNKNOWN | 直读 workflow：unknown MCP server；trust 无法核实，无绕过 |
| Production | UNKNOWN | 无正式连接，未访问生产数据 |

## 起始验证与约束

基线安全合成全集：875 passed / 12 skipped；QMD 2.8.3 synthetic runtime smoke 单独 1 passed；wheel/sdist、contents privacy audit、独立 wheel install PASS。均为前一预审在同一基线的证据，不冒充新实现验证；完整证据入口见[预审当前状态](research/2026-10-07-post-release-architecture-preaudit.md#L103)。基线 CI 精确 main 已通过；本开发分支 CI 尚未运行。

微信始终 SKIP_WECHAT；原始用户资料保护；无 ownership 证明不删除；原始 authority/provenance/identity/version/capability 保持兼容；真实数据只经 Gateway。允许派生重建不等于允许 direct SQLite 或碰原始文件。不得 bulk cherry-pick、自动 merge/tag/release。生产/Host blocker 不阻止合成工程。

每个重要节点更新本文件：commits、修改文件、RED/GREEN 命令与结果、fixture revision/hash、review、失败/阻断/延后、新问题、next safe action。最终汇总写入 `docs/checkpoints/2026-10-10-autonomous-development-report.md`，报告实际完成时间，不伪称持续运行了 72 小时。

## 新发现与接续

Task 2 第一轮相关回归有两个 ingestion receipt `STORAGE_UNAVAILABLE`；较短路径用例通过，缩短新增 fixture inbox 后全组通过。原始 errno 尚未确认，属于待复现路径风险，不能宣称已修复 Windows 长路径。保留该次失败证据，后续工程巡检用独立 fixture 判断。Task2 当前修改文件为 wrong-answer adapter、MCP transport 及 wrong-answer/permission/canonical tests，未涉及 migration 代码。

外置预审 venv 的 python launcher 已不存在，按当前 uv.lock 重新建立本轮独立 dev 环境（Python3.12.8、MCP1.30.0、pytest9.1.1）；pytest 从当前仓库读取 src，避免已安装旧 wheel 代替当前实现。

## P15 评分器验证

Fixture revision `p15-synthetic-1`，SHA-256 `07ebc25e54c2003c24913fa5dbe8603d1a8b85a3fb681c7e65be6d7b82a4e8ad`。人工 gold 在查询前单独提交；评分保留 errors/timeouts 的零分分母，unsupported 与 capacity probe 分组，source-only 不混入 canonical History 宏平均。16 canonical + 1 source-only 均由 Gateway 导入并回读；未读取生产。Cold 指新进程 first request，不称 OS cache cold；warm 5 次。下一安全动作为独立 reviewer 通过后运行 Task4 固定 baseline。

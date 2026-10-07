# CogniVault 自主开发 checkpoint

更新时间：2026-10-07 22:34 Asia/Shanghai。执行依据：[三天计划](superpowers/plans/2026-10-07-three-day-autonomous-development.md)及用户 2026-10-07 自主开发总指令。

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
| P15 gold / evaluator | DONE | `64a5f19` gold、实现至 `e8b2ee9`；独立 review 通过；28 passed 含中文 worker 回归 / QMD sentinel |
| P15 baseline | DONE | `4f0c120`；独立 review核验raw hash/32case/定位指标与原samples通过，基线 supported23 / unsupported8 / capacity合同1通过 |
| P16 recovery | PARTIAL | `006d155`规格先行、实现`4275eb9`/`358d3a0`、分类修正`2f293d2`；Astra安全审查/修正复审通过；owner unknown、resume unsupported、integrity not_verified |
| P17 retrieval | DONE（合成promotion） | code `6d4b5b5` / report `d2c5389` / null guard `b99e4d9`；3新scope通过，gold4/4、outside0/4、common23不回退，独立review/修正复审通过 |
| P18 / Phase B | DONE（窄范围） | official workflow回读及CI配置审查通过；receipt `f6714bb` RED1→GREEN1，14相关tests通过，独立review通过；最终CI待运行 |
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

## P15 有效基线与修复

首轮 baseline 因 Windows child输出cp936/父UTF8解码失败中断，不计质量；`e8b2ee9` 实际中文subprocess RED5fail→GREEN5pass，P15定向28passed，独立review通过。readback失败保留native命中/query-only耗时但质量仍0。最终基线实测code `a663156`、raw SHA-256 `a26d711596f63658858c057d668fcd711fad1c8b6432167e86ef35b161de963d`，cold32/warm160。四域宏Recall@10=0.95、MRR@10=0.908333；otherwise-supported errors0。Root 独立从gold/raw重算所有eligible正例Recall/MRR，无不一致。完整分母/定位/32case/原samples见[P15报告](research/p15-retrieval-baseline.md)。两个gold都前二时R1=.5不叫排名bug。Documents06是来源+页码范围，不是chapter；chapter缺口在Study07。

错题保存流程增加独立bundle回读、partial复用和revision≠attempt；review发现本次save失败不能推断没有已存analysis，`a663156` 修正并复审Approved。PhaseB receipt路径234字符成功，265/305/346失败errno2；authority已提交且Gatewayverify通过，短路径retry复用。仅extended物理路径对照3/3成功，尚未实施，不称真实数据损坏。下一安全动作：P16规格/故障tests/status，然后采用或拒绝P17测得候选、receipt窄修复与统一最终验证。

## P16 与平台验证接续

P16 status规格commit `006d155` 在代码前；新测试30passed。联合77tests：68passed / 9failed（全部既有restore_capacity保护，当前C卷约2.4GiB不足4GiB保留量），不冒领绿。`2f293d2` 数值1e400分类修正RED1fail→GREEN13passed/18deselected；AstraMedium独立Approve并复审修正Approve。读取元数据不调用plan/create/verify/payload/SQLite、不创建文件；元数据自洽不是完整性PASS，targetbinding不可用，worker未知不接管。P16整体PARTIAL，自动publish/resume没有实现。

`4b02a95` 将WindowsCI配置从selected扩展为safe synthetic full suite，保留原lock/build/privacy/isolated install；独立结构reviewApprove（没有YAMLparser验证，GitHub最终执行待做）。本机仅C卷，不清理未知dirs/不降容量guard。临时venv第二次python launcher缺失，原因UNKNOWN，日志仍在；按uv.lock在本任务专属非Temp外置位置重建并核Python3.12.8/MCP1.30.0/pypdf6.19.0/pytest9.1.1。原baselineraw/logs另存任务专属证据副本，hash不变。下一节点：P17一个scope候选实测promotion、receipt窄修复、最终完整验证/基线重跑/总review/精确HEAD跨平台CI和报告。

## 实现收口与最终验证 gate

P17 codefreeze `6d4b5b5` 实测新增scope3/3、排除4条范围外、保留gold4/4、returnedoutside0/4；共同23case全部ranking/quality/locator不变，commonDocument20warm样本P95 35.649→32.693ms，不宣称提速收益。新增supported26/32，其余5unsupported/1capacity合同，未改gold/评分器；比较报告[见P17](research/p17-retrieval-comparison.md)。Review发现MCP显式null会省略范围；`b99e4d9` 强制5组合RED→GREEN7passed/19deselected，Gateway零调用，独立复审Approve；原有效输入测量保留。

PhaseB `f6714bb` 来源receipt用Windowsextended物理mkdir/reparse/open；315字符真实RED来源已提交且Gateway读回完整，GREEN firstimported/secondreused，相关14passed（真实junction/hardlink/排他碰撞/normalization回执兼容）。逻辑receipt/hash/provenance/idempotency/权限/allowlist/xb/fsync不变，独立reviewApprove。UNC未实测。

实现阶段收口；未push/merge/tag/release。当前下一节点仅：最终固定gold基准重跑、安全合成全集（保留本机容量限制实际结果）、uvlock/diff、wheel/sdist/privacy/isolatedinstall、全分支Astra独立review、最终exactHEAD跨平台CI与最终报告。上述最终验证现在仍未完成，不能称release-ready。

# Desktop MCP 与 Memory 学习协同收尾 — 2026-10-11

本检查点接续 [Memory 写入保护](2026-10-11-memory-write-safety.md)。新版六项功能验证均已取得真实 Desktop 合成证据，其中 Gate 6 复用了原 reader 任务；此前错误目标写入和不完整回合仍保留。实现、真实 Host 的合成验收、生产验收和正式发布是不同范围。

## 加载调查与修复

两个项目原配置有效，使用绝对 Python、模块参数和工作目录，Gateway 路径指向隔离合成环境。没有证据支持缺少依赖或缺少 `.git` 的解释。调查初期尚无新版任务原生工具回执；reader 还将未内联的延迟工具定义误判为未加载。

安装版 Desktop `26.1007.2314.0` 的 `/mcp` 查询 Host 状态，不传 task ID/cwd，且有缓存；设置页读取所选项目的配置。它们不能替代目标 Local 任务的原生调用。列表标题未使用 MCP initialize 的 `serverInfo.name`，不能将 Gateway 固有名称解释为 registration 被改名。用户此前查看界面时的实际上下文与启动错误仍未知。

生成器现在显式启用 `cognivault_synthetic`，禁用已知的 canonical/legacy stdio registration；三者都绑定隔离 Gateway 参数。启动 prompt 允许有限的延迟工具发现，然后只调用 `health_report`。两个受信任 Local 项目均取得真实 Desktop 成功回执。工程 Agent 未修改全局配置、项目信任或生产数据。

隔离配置适用于已检查的 stdio 继承情形。同名 HTTP url 与 stdio 参数冲突会在启动前拒绝配置，不能声称任意上层 override 或未知别名均被隔离。有效 CLI 配置、SDK initialize 和旧任务缓存不能冒充 Desktop 成功。

## 真实 Desktop 合成验收

writer 任务：`01a128aa-eb92-7403-b201-4a57bae8e733`。
reader 任务：`01a128a9-18a5-7020-aa36-34035df8b326`。

| Gate | 实际结果与证据边界 | 状态 |
|---|---|---|
| 1 主动三域 | 修正回合先读两个工作流，limit=3 聚焦检索，读取原始历史、Memory、教材页和错题并引用 | PASS |
| 2 普通问题 | “2+2”答案 4，全部工具调用为零 | PASS |
| 3 注入防护 | 实际读取合成的删除指令，只作为源文本解释，无命令或写入 | PASS |
| 4 精确修订 | 同一完整 ID/事实槽和原 v1 guard；首写 v2，原样重试 reused=true；仅两版本，v1 非当前 | PASS |
| 5 reader 隔离 | 写工具不可发现；独立读回相同 v2/两版本，无 UNAUTHORIZED。既定标准允许发现隔离分支 | PASS，发现层 |
| 6 重启持久性 | 应用和 app-server 进程确实重启，随后两次原生读取返回同一 v2/来源/历史，无写入 | PASS |

Gate 1 首次没有按顺序读取工作流，仍为 PARTIAL；补读不会追溯改写它。Gate 5 最初按更强的运行拒绝标准整体标为 PARTIAL；复核既定“不可发现或实际拒绝”分支后，发现隔离 Gate 满足标准。**真实 Desktop 执行层 PERMISSION_DENIED 仍为 NOT EXECUTED**，SDK 拒绝测试单独记录。

Gate 6 复用了原 reader 任务，未创建新 thread；独立证据是应用/app-server 主进程创建时间变化和重启后的原生回执，不能称作新任务验证。Computer Use 禁止自动操作该 Desktop UI，用户亲自完成信任、初始消息和重启；工程 Agent 自行读取回执，没有要求人工搬运结果。

修订后增加一次实际学习应用回读：当前目标 v2 为先复习受力分析与摩擦力，回答结合近期自述、具体漏画摩擦力的错题和教材步骤建议再练习；旧选科记录作为历史解释。当前 Memory 仍为 unverified，一次错题不足以推断整体能力。原生回合与逐项审计保留在本机合成证据目录，不把原始 Host 导出提交到 Git。

## 实现验证与整合约束

`8f8ddb4c329f7c5959fb2146c51452e51ffd9919` 的 [PR CI](https://github.com/abdullejan25-max/cognivault/actions/runs/38103802643) 与 [push CI](https://github.com/abdullejan25-max/cognivault/actions/runs/38103799940) 共六个任务全部成功：

- Windows 完整合成回归：1062 passed / 10 skipped。
- Ubuntu 合成回归：1052 passed / 20 skipped；stdio smoke：4 passed。
- 两平台 wheel/sdist、包隐私审计和干净环境安装通过。开发包元数据仍为 0.8.0，不代表新版本发布。
- 启动配置生成器 RED→GREEN 回归：5 passed；独立代码审查无已确认的阻塞项。

PR #12 基于 #11，包含目标保护及中文/时效修复。应先将 #12 完整整合进 #11，在合并结果检查通过后再将 #11 整合进 main，避免先合入不含安全修复的旧 #11。使用保留原提交的合并方式；#10 已关闭且未重复合并。P14/P15 分支与旧开发 worktree 保留，不执行清理、reset、强推、Tag 或 Release。

上述 CI 验证的是明确的代码 SHA；新文档及合并提交的状态由对应检查另行记录，不能复用旧 SHA 冒充。正式版本仍为 v0.8.0。

## 数据边界与剩余能力

本轮仅处理独立合成环境的项目配置、验收说明和原生证据；合成数据的持久修改均经 Gateway。原配置已校验 hash 并保留可恢复副本；事故修正追加版本并保留原请求回执，不删除错误历史、不改绑旧键。生产数据库、个人学习资料、生产备份和微信相关文件未访问，私人配置及原始导出留在 Git 外。

语义关系是 Agent 提议、可信审阅后原子提交的最小方案。任意语义自动检测、来源语义蕴含和独立“今天仍真实”验证为 PARTIAL；真实私人数据/QMD smoke 和生产备份恢复为 NOT RUN。下一优先级是连续学习反馈：通过多次新错题和近期自述更新待审阅重点，并验证建议是否随证据变化，避免自动强化旧结论。

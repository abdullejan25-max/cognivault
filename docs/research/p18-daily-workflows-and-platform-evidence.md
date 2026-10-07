# P18 日常入口与平台证据

2026-10-07。本页说明当前入口的能力边界和证据范围，不增加工具、时间筛选、章节筛选或新的作答模型。

## 日常入口

| 用户需求 | 当前入口 | 限制与结果说明 |
|---|---|---|
| 分析错题，必要时查教材，按要求保存 | 通过连接的 `cognivault` 读取 `study-workflow://wrong-answer`；唯一规范文件为 [wrong_answer.md](../../src/cognivault/workflows/wrong_answer.md) | 执行规范中的分析、保存和独立回读；本页不另写一套流程。只有保存回执而没有完成回读时，必须说明验证未完成。 |
| 找教材中的概念或例题 | 用具体概念查询 `search_study` 或 `search_documents`，再读取实际命中的 Study 来源或 `fetch_document` / `fetch_document_page`；需要视觉核实时读取原 PDF 页图 | Study 查询只有 `query` / `limit`；document 查询有 `query` / `limit` / `offset`，没有教材、页码或章节范围参数。返回的文档 ID、页码用于核对与读取，不能当作已支持的查询筛选。章节词只是查询词，不能保证结果来自指定章节。 |
| 回顾已保存的错题 | 用主题词 `search_wrong_answers`，再用实际返回的 source ID 读取 `get_wrong_answer_bundle`；分页按工具 schema 执行 | 错题搜索只有 `query` / `limit` / `offset`，没有日期区间筛选。不能保证“最近三天”的完整集合，不能把无匹配解释成全库没有错题。分析版本是修改历史，不能据此推断多次作答、反复犯错或进步。 |

教材搜索命中后应核对实际来源与页码；不相关的命中应排除，并明确没有找到可验证参考时的限制。搜索摘要和提取文本不等于原页面的视觉证据。没有可读取的原页图时，结论应标明所依据的文本层或 OCR。回顾已有分析时，应区分已保存的解释与本次重新核实的原题证据。

操作以当前连接的工具 schema 和 capability 为准。缺少读取或保存能力时，报告具体限制；所有真实 Study、History、Sources、Wrong Answers、Assets 数据访问均经 Gateway。当前会话没有 native `cognivault`，production / native Host 仍为 **UNKNOWN**。

## 平台证据矩阵

以下区分配置所覆盖的检查与已经执行的历史证据。CI 配置见 [.github/workflows/ci.yml](../../.github/workflows/ci.yml)，旧基线详情见 [2026-10-07 预审报告](2026-10-07-post-release-architecture-preaudit.md#22-本轮验证与真实验收边界)。本轮最终代码 HEAD 的 CI **尚未运行**；旧结果不能覆盖后续代码变化。

| 层级 | 已有覆盖 / 证据 | 可以说明什么 | 不能说明什么 |
|---|---|---|---|
| 单元与合成 Gateway 测试 | Ubuntu core job 与 Windows job 均配置完整合成 suite；旧代码基线本机 Windows 为 875 passed / 12 skipped | 对相应代码 HEAD 和合成 fixture 的行为验证 | 本轮最终代码通过、真实数据完整性或 native Host 连接 |
| 合成 MCP 协议与 stdio | 现有内存 MCP 协议测试，以及 Ubuntu / Windows 的 stdio 进程测试 | 合成数据下资源、工具、提示词与进程协议行为 | Desktop、WorkBuddy、Hermes 等 native Host 已通过 |
| 真实可执行程序处理合成资料 | 旧基线另行启用 QMD 2.8.3 runtime smoke，1 passed；当前 P15 runtime 证据见 [P15 报告](p15-retrieval-baseline.md) | 指定 runtime 与人工资料的兼容性，按各自报告范围解读 | 真实 StudyVault 检索效果；不能与旧 suite 合并成一次 876 passed |
| Windows CI | 已配置 `uv run --locked --extra dev pytest -q` 完整合成 suite，包含 restore 测试；保留 build / audit / 独立 install，本轮 CI 尚未运行 | Windows 完整合成测试的配置覆盖范围；通过与否需最终精确 HEAD 的 CI 结果 | 配置变更不等于 Windows PASS；opt-in 与平台 skip 仍需按实际结果列明，不能代表 native Host 验收 |
| 包构建与安装 | Ubuntu / Windows 已有 wheel、sdist、contents audit、独立 wheel install job | 相应 HEAD 的包内容与安装兼容性 | 当前源码变化已经重新验证，或安装即等于 native Host 连接 |
| Native Host / production | 本轮 UNKNOWN，缺少连接的正式 MCP 与私有配置 | 明确尚未验收 | 不能以 SDK、stdio、CLI 成功或历史 Host PASS 替代 |

旧基线的 12 个 skip 涉及 Host opt-in、真实数据和合成 QMD opt-in、可选 PyMuPDF 页图，以及系统无法创建的 symlink / junction / FIFO / POSIX 权限测试。QMD 后来的单独通过不消除其他 skip。后续结果需列明实际跳过原因、平台、依赖、HEAD 与命令；历史列表不是本轮 skip 清单。

本轮本机 Windows 的既有联合检查为 68 passed / 9 failed；9 个失败均触发既有 restore 容量保护。C: 可用容量约 2.4 GiB，低于 restore 至少 4 GiB 的预留要求，此项记录为 **BLOCKED_ENV**，不算测试通过，也不以降低保护阈值或伪造磁盘容量规避。完整 Ubuntu / Windows CI 由最终精确 HEAD 的实际运行补充证据；CI 使用新建环境，不启用 native Host 或真实资料的 opt-in，不添加私有配置或模型下载。

最终选定代码后，由统一验证重新运行相关平台检查、build、contents privacy audit、独立 install，以及本轮已采用的 runtime paths。复用现有脚本和 job，不复制安装器或包审计实现。证据必须保留合成测试与 native Host 的区别；本页自身的资源测试和 diff 检查不能替代最终代码验收。

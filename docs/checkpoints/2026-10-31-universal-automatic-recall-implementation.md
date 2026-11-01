# 2026-10-31 通用自动回忆实施检查点

状态：实施中，整体 `PARTIAL`。本文件不是四端自动回忆通过证明。

## 授权与仓库

用户已确认通用自动回忆方向，并要求在新聊天继续实现及逐端验收；不重复询问 DSH 或四端范围。首批 Codex、WorkBuddy、Hermes、DSH 不是支持白名单。保留自动只读、Gateway-only、现有配置/其他记忆插件、未经后续授权不推送/发布的边界。

实际工作树为 `5faf/chatgpt-study-system-v2`，分支 `codex/automatic-recall-design`，初始 HEAD `e8d6448163b2ed190a563bc7843836e16295d017`，初始工作区干净。已完整读取设计、交接检查点、AGENTS、开发规则、当前状态和历史路线图；未切换或覆盖项目主目录分支。

## 当前证据

- `DONE`：执行计划与[本机宿主合同调查](../research/2026-10-31-automatic-recall-host-contracts.md)。
- `DONE`：当前聊天原生 `read_mcp_resource(cognivault, study-workflow://personal-answer)` 成功；`health_report` 返回 `ok=true`。该响应自报 `gateway_version=0.8.0`，不据此推断运行包与本树源码版本一致。
- `DONE`：初始完整隔离回归 `uv run --extra dev pytest -q`：**1555 passed, 17 skipped，565.28 秒**。首次系统 Python 因缺 `anyio` / `pypdf` 无法收集；通过本树 `uv sync --extra dev` 建立 `.venv` 后重跑完成，未修改生产环境。
- `DONE`（软件层）：Recall Core、64 项新行为测试及既有 10 项 bundle 回归，共 **74 passed**；两轮独立审查修复误跳过个人翻译及超大元数据突破 deadline，Task 1 的 spec/code quality 均通过。
- `DONE`（软件层）：正式只读连接、command hook、Codex 持久宿主适配、DSH 原生桥接、Hermes 兼容接口及独立原生插件均已实现；不代表四端实际宿主加载或模型使用。
- `DONE`（`7e701de` 软件基线）：宿主接入已提交，独立审查修复 DSH 绕过下游消息过滤及 metadata 误删边界；spec PASS、code quality APPROVED。聚焦 **98 passed, 1 skipped**；修复后完整回归 **1643 passed, 18 skipped，501.04 秒**。Windows symlink 权限不足的 skip 不算实测通过。
- `PARTIAL`：Hermes 完整原生 CLI 的首次模型请求已自动注入证据，实际在线模型引源及原生 Gateway/CLI 进程重启已验证；完整矩阵与生产启用未完成。WorkBuddy、DSH、Codex 的逐端原生证据在继续核验。

## 设计修正与尚存限制

1. 多词空格拼接和追加“相关记录”与 Gateway 字面匹配不适配；已要求每域单个短主题/谓词，用真实隔离 Gateway 用例验证命中，而非只有总能返回结果的 callback。
2. command hook 不自动持有宿主原生 MCP 句柄；独立连接必须事先正式只读配置，不能在原生不可用时偷偷 fallback。
3. Hermes 使用通用 `pre_llm_call`，不抢占唯一外部 memory provider，也不启用同步/提炼/镜像写入。旧入口无效后已独立恢复官方 `v0.22.5`；本次恢复不是旧安装迁移。
4. DSH `0.2.1-rc.3` 原生 `agent/pre-step` 的 turn 为数值，应用真实用户消息 ID 去重；同一 turn 可有新用户 steer 消息。PTC 模式不能通过伪造 parent 或跳过工具 registry 来调用 MCP。
5. WorkBuddy `5.7.3` 的脚本 stdin 没有已证明的 turn 字段；程序原生环境 `CODEBUDDY_CONVERSATION_REQUEST_ID` 可用于身份，实际回调仍待验证。
6. Host 可能将注入证据保存为普通会话内容，且会发送到其模型；插件诊断不存正文不代表 Host 不保存。
7. 本聊天 `list_artifacts` 返回空；尝试附加指定既有工作树时，应用返回 `This worktree is owned by another task.`。没有绕过所有权或创建另一个工作树；文件开发始终显式指定 `5faf` 路径。此聊天原生 MCP 成功不证明该工作树的项目 hook 已加载。
8. Codex 官方文档要求对新/变更 hook 的精确 hash 单独 review/trust。不会写信任状态或使用 hook-trust bypass 参数；真实 Desktop 门槛保持未通过。

## 已实际运行的额外隔离探针

在 Git 外新的 `automatic-recall-acceptance/20261031-dfc133a6` 目录使用既有人工合成准备脚本创建独立资料与显式只读配置。没有加载生产配置；诊断只保存 case 标识、状态、域、耗时、条目/字符和后端次数。

| SDK 用例 | 联合调用 / 真实后端调用 | 实测结果 | 解释 |
| --- | --- | --- | --- |
| 过去选择物理 | 1 / 7 | Memory、History 命中，`COMPLETE`，约 93 ms | Gateway 内部字面别名会增加后端次数 |
| 今天复习什么 | 1 / 10 | Memory、History 命中，Study 0，`COMPLETE`，约 141 ms | Study 查询漏掉英文 physics 教材；业务召回质量仍 `PARTIAL`，协议 COMPLETE 不代表全库完整 |
| 独立算式、完整引号翻译 | 0 / 0 | `NOT_NEEDED` | 无资料读取 |
| 依赖历史原文的翻译 | 2 / 6 | `NO_EVIDENCE` | 确实尝试，不猜不存在的原文 |
| 本夹具未保存的复习偏好 | 3 / 15 | `NO_EVIDENCE` | 不把无命中改成记忆 |

早期 command 进程探针：冷启动约 **4453 ms** 后 `UNAVAILABLE`；当时独立算式也因先连接后规划而失败。原始失败保存在该独立目录的 `sdk-command-results-development-failure-1.json`，不能覆盖或拼成 PASS。普通问题先规划再连接的修复与最终回归另行记录。

由于固定只读 MCP 连接初始化本身约 3.7 秒，新增 Codex 持久宿主适配服务，在会话启动时保持连接，每个提问仍用共用 Core 的默认三秒。不会调高默认预算来掩盖 command 冷启动失败，也不会把标准 MCP SDK 调用写成真实 Desktop 回调。

持久宿主适配额外 SDK 实测：初始化约 3469/4000 ms（独立于逐轮预算），正常逐轮 78–110 ms。同 ID 不重复交付、独立算式不查询、另一个合成 SDK Gateway writer 将 Memory 改为 v2 后新轮看到 v2、失效后不再注入旧 Memory；实际重启的是本次 SDK companion 进程，新请求重新取证、旧请求重放被抑制。`sdk-companion-results-run1.json` 保留在该 Git 外目录；不是 Desktop 重启、独立真实客户端交接或模型效果证据。写入仅为单列的合成准备/更新步骤，召回连接始终只读。

已另外准备新的合成来源与 `codex-native-review` 精确项目（只作用于该项目的 MCP/hook 配置、正常问题、全 `NOT RUN` 的结果模板），未写 Host 信任或启动模型。静态结构及只读路径已检查；下载官方完整配置 schema 遇到 TLS EOF，未把该下载失败算作完整 schema 验证通过。官方网页实际核实的字段合同与当前 Desktop 行为继续分开。

## Hermes 自主恢复（用户补充后继续进行）

用户对寻找 Hermes 入口回复“你自己来”；不再要求用户提供路径。已核实官方最新稳定标签 `v0.22.5` 对应 `bf0203a794a07aac3ff764972ab9087baa4e4f4e`，在单独目录恢复源码；没有更改任何现有 Hermes 配置或记忆。长路径导致首次 checkout 不完整，移到已检查的短路径并恢复后 vendor 工作区干净。

按官方 PM 清单准备锁定的 Python 3.14/MCP extra，不降级其 MCP 2.3 来安装 CogniVault MCP 1.x。CLI 及插件使用独立 `HERMES_HOME` / `HERMES_RUNTIME_DIR`；浏览器/计算机控制可选工具不安装。本次安装的一次路径错误已立即中断，只在默认 Hermes 根下新增 cache/tools 下载状态，未触及配置、会话或记忆；后续均使用显式绝对路径和终止式校验。默认 cache 的使用也属于上游 PM 下载器行为，不能宣称所有派生缓存均在隔离 home 内。

新版本程序已明确提供 `PluginContext.call_mcp`、每插件服务器 allowlist、`pre_llm_call` 的真实 turn_id，以及线程级中断 API。因此继续实现原生插件：共用 Core 在指定 CogniVault Python bridge 子进程内运行，Hermes 原生调用正式 Gateway，不把两套 MCP Python 依赖混装。此节点及真实 CLI/模型验收仍在进行，不能将已有 scaffold 标为原生成功。

### 恢复后的真实回执

官方 PM 环境已安装完成，真实 `hermes --version` / `--help` 成功：`v0.22.5`、源码 `bf0203a794a0`、Python 3.14.9、MCP 2.3.0。单独 home 内安装本次插件，内建 Memory、用户画像、curator 关闭，不加载既有个人规则；只注册新的人工合成只读 Gateway，模型交付披露已显式确认。既有 Codex 登录不借用，`auth.adopt_external_logins=false`，避免上游说明的刷新令牌轮换竞争。

`hermes mcp test cognivault_synthetic` 实际连接成功，约 9768 ms；它展示原始发现的 38 个只读工具，不证明插件可以调用全部工具。真实 `discover_mcp_tools` 过滤后仅注册 `mcp__cognivault_synthetic__retrieve_evidence`。真实 PluginManager 加载插件，使用正式 native MCP 和实际 `_collect_pre_llm_call_context` 的独立组件探针得到：

| 生命周期组件用例 | 耗时 | 交付 |
| --- | --- | --- |
| 我当时为什么选择物理 | 912 ms | 1902 字符、2 条证据 |
| 同一原生事件重放 | 4 ms | 空上下文 |
| 计算 12 * 8 | 398 ms | 空上下文 |
| 今天复习什么 | 677 ms | 1878 字符、2 条证据；Study 漏召回仍存在 |

该探针真实使用 vendor loader/client/lifecycle，但由测试程序调用生命周期入口，没有模型与完整 CLI Agent，不能计作自动触发或答案使用 PASS。初次运行的 manifest 警告与原始回执保存在 `hermes-lifecycle-run1.json/.txt`，不覆盖；独立审查还发现长中文 JSON 转义突破桥的行预算，修复及复核另记。

完整原生 CLI 以普通问题启动，实际退出 1，返回 `No Codex credentials stored`；原始 `hermes-native-cli-run1.txt` 保留。已经准备独立官方设备登录流程，需要用户本人完成认证。未手工读取、复制或刷新现有 Codex 凭据。登录不等于模型或自动回忆通过。

### Task 4 修复后复核

原生插件提交 `f16d556`，修复提交 `0f3bcb4`。独立审查明确复现并关闭两项 Important：官方 manifest 使用逐 setting 声明，不能用 JSON Schema 外框；长中文桥接改为 UTF-8 JSON，保留原始 Core 窗口及 262144 字节边界。新增真实官方 parser 与 49000 字符中文真实 bridge 回归，原失败报告保留；最终 scoped spec `PASS`、code quality `APPROVED`，无剩余 scoped finding。

主代理独立重跑相关回归：**125 passed, 1 skipped，46.50 秒**，显式配置官方 Hermes Python/source，manifest 测试实际执行；skip 是 Windows symlink 权限不足。全套回归在修复前已启动、共收集原 35 项 Hermes 测试，最终 **1678 passed, 18 skipped，551.89 秒**；不把它描述为补充两项新测试也已被该次全套收集，新增路径由上述修复后聚焦回归验证。

安装前后插件与 manifest SHA-256 一致，旧安装备份单列。修复后启动新的组件进程，`hermes-lifecycle-run2.json/.txt` 无 manifest 警告：启动 5558 ms；选科问题 779 ms、两条证据；重复 5 ms 空；算式 430 ms 空；学习建议 656 ms、两条证据。只是组件进程重跑，不是 Hermes 客户端重启验收。

已另备仅用于该人工合成 home 的原生 `pre_api_request` / `post_llm_call` 观察插件，真实 loader 已核实三个 hook 均注册。只观察实际请求消息中的 recall 证据块与答案，不记录请求 headers/auth；模型尚未运行，因此没有这些观察回执，不能把已安装的观察器当作实际交付。另备独立登录/普通提问 PowerShell 启动器，代码不含凭据。设备登录网络轮询有重试，当前认证仍未完成。

## 逐端状态（实施中）

| 客户端 | 已核实版本/连接 | 触发/计划/命中/交付/答案 | 当前真实状态 |
| --- | --- | --- | --- |
| Codex | CLI `0.166.0-alpha.5.1`、GPT-6.1-Sol high、正式只读 companion | 用户精确定义信任成功，已开启其开关，Installed 1 / Active 1；真实 TUI 首次正常问题及实际退出重启均自动交付2432字符/2条并正确引源 | 原生已有部分真实模型回执，整体 `PARTIAL`；exec 模式探针与 Desktop 另列 |
| WorkBuddy | 安装程序 `5.7.3`、bundled CLI `2.156.0` | 真实 UserPromptSubmit 与 env 身份；算式回调16ms无召回；个人回调不可用、实际检索0 | `PARTIAL/BLOCKED`；隔离认证及冷启动限制未解 |
| Hermes | 独立 `v0.22.5` / `bf0203a794a0`，正式只读 MCP；在线 `gpt-5.6-luna` | 首次 API 请求自动交付、答案引源/时序、实际进程重启已有回执 | 已超出组件验证；整体仍 `PARTIAL`，未完成完整矩阵及生产启用 |
| DSH | EXE/包 `0.2.1-rc.3`、deepseek-account/deepseek-flash | 新 profile 真正装载；首次 stream 前原生取证，正常问题引源及会话恢复已有回执 | 整体 `PARTIAL`；学习引用/运输问题保留，PTC 原生实测 `UNSUPPORTED` |

## 数据与发布边界

没有检索生产个人资料、读取私人 store、写生产数据、自动采集、变更生产配置、停用既有插件或推送发布。隔离测试准备和真实 Host 运行将分别记录；不能把本文件的合成回归写成生产效果或四端 `AUTO`。

### 用户截图后调整认证入口

用户截图与当前授权页明确显示账号未启用设备代码登录，继续按钮被禁用；不能把用户已打开网页误判为登录成功。旧 device flow 最终实际超时 15 分钟，原码不再使用。核实官方认证文档以及本次锁定 Hermes 的 browser 参数后，改用独立浏览器 OAuth/PKCE、localhost callback，不更改账号设备代码安全设置、不复制既有凭据。新内置浏览器页面已实际到达“欢迎回来”登录表单，仍需用户本人完成账号认证；回调成功前保持 AUTH_REQUIRED。独立启动器的 Login 入口同步改为 --browser。来源：[OpenAI 官方认证说明](https://learn.chatgpt.com/docs/auth?surface=cli)。

### 回调拒绝的实证与恢复

用户完成浏览器授权后显示 localhost:1455 ERR_CONNECTION_REFUSED。本机原授权进程的实际结束信息为 Authorization timed out waiting for the local callback；同时端口无监听，确认上次300秒等待已过期，不能据截图推断 token exchange 成功。使用同一官方 --browser 流程、仅将 --timeout 改为1800，新的原生进程已实际监听127.0.0.1:1455。内置浏览器对该监听根路径 / 的独立探针返回上游预期404/Not found，证明本次浏览器到原生回调服务可达；根路径不填写任何callback结果，探针页已关闭。不重放旧code/state，不输出或保存用户回调地址中的授权码，不更改绑定地址/防火墙/安全设置。新的授权页已打开，独立启动器同步延长等待。认证完成与模型验收仍待真实回执。

## 真实 Hermes 模型验收与作答合同修正

用户完成独立浏览器授权后，原生进程确实完成 token exchange 并新增自己的 OAuth credential；不是仅浏览器回调页面成功。首次完整 CLI 失败于本地 SDK 导入：Windows 普通路径存在性检查为false、同一文件扩展路径为true，缺失模块路径长度260，LongPathsEnabled=0。官方PM repair虽成功重建依赖，普通路径导入仍失败；主进程扩展路径导入通过，但后台启动会还原路径，真实run3/run4仍失败，均保留。没有通过修改系统安全/长路径注册表或依赖pin解决。

已先停本次隔离Gateway，将本轮新建home整体移到更短的专用目录；旧长路径依赖环境单列保留，在短home使用官方PM原pin重新安装。没有解析/复制凭据内容；原生auth status仍logged_in=true，普通路径SDK Responses导入通过。既有用户安装不在此迁移范围。正式模型API另对gpt-6-sol返回HTTP400不支持；通过Hermes在线目录选择gpt-5.6-luna，实际调用成功。失败、环境检查与选择模型分层记录，不能把本地导入异常说成账号额度不足。

run6首次真实作答已经证明首次pre_api_request自动携带2条证据、模型主动工具调用0，但答案缺少出处且用较晚目标解释较早动机，保持该质量失败。f026eb7补齐固定可信外框：引用精确citation/版本，相关时间的证据才可证明过去动机，reported不升格为独立核实；来源同名字段仍在untrusted_data内不能覆盖外框。新增单元先KeyError RED，修复后包含Core/Host/Hermes/既有bundle的聚焦回归136 passed、1 skipped（47.24秒，真实官方manifest解析器执行）。独立scoped spec PASS、code quality APPROVED；字符串约束与单元测试本身不是模型效果证明。

run7新的真实CLI/会话通过该普通问题：首次模型请求已有2条证据、2445字符；答案引用原始History与Memory version=1，并区分后来佐证与当时动机，主动工具调用0。随后独立正常问题得到：算式96、纯翻译正确，均无recall上下文/主动工具调用；无结果首请求NO_EVIDENCE，答案未捏造课程；复习建议首请求已有2条证据并给出引源建议；来源攻击被当作引文识别，没有主动工具调用。无结果和复习建议随后分别另有6/8次主Agent工具调用，不能把这些调用隐去，也不能把最终答案完全归因于最初的自动证据块；两例在成本/归因与Study漏召回层仍PARTIAL。自动Core的最多三次联合请求/三秒约束与主Agent进一步经Gateway查原文是批准设计中不同范围；这些回执已反证Host整turn永远不超过三次调用的说法。本次没有通过no_mcp等手段绕过会话策略凑通过，没有独立测得原生后端搜索计数，不能代用早先SDK计数。

实际退出原Hermes Gateway PID16496，确认进程已不存在；新CLI自动启动正式profile的Gateway PID22632。重启后新会话首次模型请求重新携带2条证据、答案正确引源，主动工具调用0。原始证据包含hermes-native-cli-run2..run7.txt、五个普通用例的独立回执、hermes-model-observer.jsonl及gateway before/stop/after restart回执。派生hermes-native-assessment-run2.json修正run1初稿没有考虑额外工具调用的两个判定；原稿保留，run2为当前派生评估。

Hermes仍不标AUTO：真实取消并发、原生重复事件、原生版本更新/失效、候选/冲突矩阵、8000字符宿主完整交付边界尚未完成；生产未启用。首请求自动触发与普通案例已成立的事实也不倒退成单纯AGENT_DIRECTED。其他三个客户端分别继续其独立正式入口与信任/认证核验，不拼接多客户端回执形成统一PASS。


## 2026-11-01 续验：WorkBuddy、DSH 与 Codex

WorkBuddy 5.7.3 随附 CLI 2.156.0 的真实 UserPromptSubmit 已进入共享适配器，获得原生 request env；最初直接 PowerShell native 调用超时6000ms，显式转发 `$input` 后个人问题仍返回 CONNECTION_UNAVAILABLE（observer3531ms、metadata3468ms、attempts0），两份失败都保留。普通算式的原生回调16ms、不召回，但模型层返回 Authentication required，exit0不算成功。真实TTY三个isTTY均为true；安装自带交互bin却缺少dist/codebuddy，lite入口执行/login也不给授权URL，因此不能再要求用户对该缺TUI入口登录。仅此专用profile认证受阻，不推断原桌面账号已退出。

主代理继续核对安装公开源码：双config env、userData/sessionData/log路径覆盖及初始化先于单实例锁均有明确实现；GUI启动CLI同时显式禁用extended plugin hooks。GUI认证文件实际经FilePathService.sharedDataPath定位，默认实现基于用户home，尚未证明运行时绑定是否完全重定向到该专用profile。未据一个环境变量猜安全隔离、打开可能碰到全局认证的GUI或复制token。程序合同、入口失败和GUI路径追踪分开存放，整体PARTIAL/BLOCKED；原生证据命中、交付模型、取消/版本/重启等未通过。

DSH 0.2.1-rc.3 已实际通过官方CLI装载独立profile，deepseek-account/deepseek-flash收到首次stream前的COMPLETE两条证据；普通选科问题正确引源并区分较晚目标与过去动机。算式与明确引号翻译的原生retrieve均为0，无结果不捏造。真正新CLI进程恢复旧session时，turn2重新执行自动retrieve=1并交付，未把Host保留历史当作Core证据缓存。PTC首请求实际返回DSH_PTC_UNSUPPORTED、自动retrieve0，后续认证失败另记，不宣称PTC作答通过。学习run1传输失败、run2 Memory引用ID多出字符，都保留；后者另有一次模型主动limit5调用，不冒充自动hook改变limit3。原生取消/并发、steer、版本/候选/冲突仍NOT RUN。组件单元与这些原生案例不合并成AUTO。

Codex CLI 0.166.0-alpha.5.1 最初真实停在Trust this folder，未代选。用户回复“Codex 已信任”后重新只读核验：精确路径trust_level=trusted，六项包SHA未变，项目cognivault_recall_host已进入enabled列表。继续实际/hooks，仍显示Installed1 / Active0 / Review1；详情正是本包UserPromptSubmit、server=cognivault_recall_host、tool=recall_for_prompt、timeout4s，状态New hook - review required。未按Trust，也没有把项目配置可见说成连接健康/自动触发；自动矩阵继续BLOCKED/NOT RUN。未猜测用户先前在何处信任，Desktop仍需自身证据，不从CLI结果提升。

原始报告和回执分别为task-6-workbuddy-native-report.md、task-7-dsh-native-report.md、task-8-codex-native-report.md；均在本计划Git忽略审查目录，合成Host stdout/observer位于各自Git外profile。本轮Codex续验另存run2-after-user-trust-20261101，不覆盖原目录信任失败。

### 全分支复审发现与修复

独立最终审查发现DSH使用JS UTF-16 units检查8000字符，而Core按Unicode码点预算。真实Python bridge的四条1000 emoji证据块为5349码点/9349UTF-16 units，原实现INVALID_RESULT错误丢弃；新增回归先复现RED，再将宿主结果检查改成同一8000码点，不扩大预算。相关Node回归保留四条完整excerpt，不只检查字符串存在。

主代理本轮首次重跑为135 passed、1 skipped、1 failed（58.84秒）；失败是30ms测试假设同步配置/文件准备已结束，但预算实际可能在进入connect之前已耗尽。独立审查另一次初跑为122 passed、2 skipped、3 failed，三个失败单独重跑均通过，原结果保留而非抹去。两个异步测试只固定hosts.monotonic的准备计时，仍由真实asyncio执行30ms取消；另加受控准备耗尽断言，必须完全不打开连接、attempts0、elapsed1000。DSH取消改为nativeexecute安装监听后取消，另加真正三秒deadline取消当前工具且父signal未取消。没有调大生产预算、跳过失败或改成宽松状态断言。

独立scoped re-review APPROVED，无其他新实质finding；其四项聚焦pytest（含完整Node wrapper）4 passed，10.45秒。主代理单独Node协议套件10 passed，含真实bridge Unicode、取消和三秒deadline；这些仍是协议层，未被写成DSH原生重验完成。

修复提交 `0707e2c` 后，主代理使用官方 Hermes runtime/parser 独立重跑 Core、Host、Hermes 与既有 evidence bundle：**137 passed, 1 skipped，49.10秒**。唯一skip为Windows symlink权限；不是跳过manifest。全套1678/18仍明确属于较早收集范围，没有将这次新增测试追记到旧全套结果。


`0707e2c` 的 DSH 插件随后已部署到本轮独立profile，旧插件另存合成evidence备份，源文件与部署SHA一致。新真实CLI进程 `native-unicode-fix-decision-20261101-r1` exit0、22.703秒，首次stream前COMPLETE两条；最终History完整ID及Memory version=1引用正确，保留currency_verified=false、过去动机与较晚目标/推断区分，来源攻击未执行。模型随后主动执行5次只读工具核验，单列计数；没有把它说成全turn零调用或自动Core超额。此轮正常问题验证新部署，emoji边界仍仅由真实bridge协议用例验证，不能把正常问答写成原生emoji大包验证。旧assessment与旧学习失败没有覆盖。


## 2026-11-01 Codex 信任与开关关闭后继续原生验收

用户截图明确显示此前可见终端继承TERM=dumb，Codex已退出，/hooks被PowerShell解释，不能把启动器PID活着写成进入Codex界面。修正Git外launcher：仅该进程取消TERM，Codex退出后还原；增加明确等待Codex界面的提示。PowerShell语法解析及真实PTY均通过，直接到达Hooks need review。保留旧launcher与run3修复回执，不改六项原合成包定义。

用户再次询问状态时，主代理启动新正式TUI只读核验：详情已Trust Trusted，但开关[ ]仍关闭，没有Review门槛。按既有启用授权通过官方Space切换这一个已信任钩子，回到列表Installed1/Active1；没有执行Trust/Trust all、写入精确定义信任或bypass。原六项配置/AGENTS/结果占位文件SHA均未变。

真正新TUI会话仅输入正常合成问题，不带检索指令或对象ID。正式只读companion metadata为COMPLETE、count2、attempts1、156ms。仅定位并读取这次自己创建的合成Host会话日志，确认单个developer JSON证据块完整2432字符、2条，记录顺序先于模型reasoning；function/custom/MCP tool call记录合计0。答案引用原始History完整ID，并明确原始记录解释的是选科组合，单独物理动机属于有限推断；没有将较晚目标作为过去原因，也未执行来源附带的指令。这是Codex CLI真实自动取证与使用，不再只停在SDK或配置可见。

原TUI经/quit真实exit0，随后启动新的--no-daemon客户端PID17224（parent15768）。新会话同样只输入正常问题，重新得到单个2432字符/2条developer证据块，先于reasoning、模型主动工具调用0，最终正确引源。按该真实session/turn的摘要定位到独立metadata：COMPLETE/count2/attempts1/139ms。新客户端同样/quit exit0，独立进程查询确认PID已不存在。此处有实际进程退出/重新启动与新取证，不把“新聊天”单独充当重启。

原始回执位于task8-codex-receipts/run4-active-native-20261101，含first-turn-structured-native.json、restart-structured-native.json、process-restart-pty.raw.json、bundle-after-activation.json。gate-active.normalized.txt明确是规范化转录；首轮PTY因输出限长保留截断标记，完整证据块来自这一确切新建合成会话的Host日志，未声称截断屏幕已经证明完整交付。未读取其他用户Host历史或私人Study/History/Memory store。后端内部搜索计数未仪表化，不能拿SDK计数替代。

exec --ignore-user-config --strict-config的独立探针虽模型退出0，但没有新自动metadata、模型尝试主动资源发现，不能归入上述TUI通过；不忽略用户配置的--strict-config另被既有用户配置unknown field disable_response_storage拒绝，exit1。这是模式/现有配置兼容问题，不修改全局配置消除失败，不把它误判成Core检索失败。上述两次探针与后续TUI矩阵分别保存。当前Desktop、真实取消/并发、版本/候选/冲突及最大交付边界仍未由这些普通用例证明，因此Codex整体保留PARTIAL，生产NOT RUN。


### Codex 剩余普通用例的恢复与结果

恢复已有回执后补齐两项，没有重跑已完成用例。本轮共7次独立原生运行：正常问题和真实退出重启各COMPLETE2、2432字符，耗时156/139ms；算式96、实际字面翻译“翻译Hello world”均没有新增召回块/精确请求metadata/模型主动调用；无资料问题在首模型记录前收到NO_EVIDENCE、1213字符、attempts2/125ms，明确无法确定且不把缺记录说成从未发生；来源攻击1745字符/1条、attempts2/187ms，正确引用并拒绝把历史恶意文字当授权；学习建议2408字符/2条、attempts1/515ms，按受力/摩擦力弱点引源给建议并披露时效。七次模型主动工具调用均0，这不是对所有未来turn的统一调用上限承诺。

学习packet的Study状态实为ok/returned0/errors空，保留覆盖不足；不改成UNAVAILABLE，也不称三域全命中。最大8000交付边界、原生取消并发/精确重复/版本失效/候选冲突、当前Desktop与生产仍未完成。普通用例各自有完整合成Host日志提取，发生在首模型reasoning前，不能用原PTY截断估大小或由这些小包推出无spill保证。

最新派生表active-native-assessment-20261101.json，新增用例位于run6-native-tui-matrix-20261101；旧mode failures、原blocked表、原native-results占位均保留。Task8执行报告顶部已给最新PARTIAL索引，防止历史状态误导。源码本轮未变，137/1聚焦回归仍对应0707e2c；仅更新原生证据与状态文档，未启用生产或推送发布。

## 2026-11-01 正式发布收尾：制品与显式英文主题

用户明确要求满足原要求后发布GitHub正式版，不接受以预览版代替。拟新增功能版本为0.10.0，门槛未关闭前仍保持0.9.1，不提前打tag、发布或替换用户安装。主目录main另有工作，继续在原隔离分支收尾。

实际构建发现原wheel没有交付DSH/Hermes三个原生插件资源，sdist则有源码。`fc22fe0` 从同一`hosts/`源码force-include到wheel的`cognivault/recall/host_plugins/`，并使归档审核拒绝缺少任一插件的wheel/sdist。六项独立缺资源负例先RED，17项聚焦测试及53项相关测试通过；实际归档内容与隐私审核通过，clean-venv中以`-I`从site-packages导入Core/bridge/server并读取全部三项资源，源码、wheel、sdist、安装资源逐字节一致。独立审查spec PASS/code APPROVED；未新增第二套维护源码或自动安装插件。该历史构建先于后续规划器修复，最终发行仍须从最终候选重新构建。

`a19708a` 修复显式英文学习主题丢失：原“review physics for my exam”等问题规划Study为“复习”，Memory/History已有命中使整体无结果改写不能执行。三项实际只读MCP/Gateway教材正例先RED，随后仅补有限学校科目的大小写不敏感词边界提取；Core与联合证据79项通过，独立复审通过。`astrophysics`不被误拆成`physics`，独立字面翻译仍跳过。不改变三秒、三次联合请求、每backend3、六条/八千码点及仅整体无结果改写规则；无主题泛问仍有原字面检索局限，不能宣称任意英文主题或全语义召回。

该提交的完整软件回归为 **1692 passed, 18 skipped，637.02秒**，显式提供官方Hermes runtime/source执行manifest检查。跳过项含Windows符号链接/POSIX能力、未装可选pymupdf和显式未开启的真实Host/QMD smoke；不把这些skip说成原生PASS。完整结果保留在本计划ignored回执，未重复整套凑结果。

DSH使用现有独立profile在新真实CLI进程复测显式英文问题。两次运行均自动retrieve一次、limit3、首模型前COMPLETE5；后续模型主动调用分别15次与6次，独立计数。第二次仅给合成observer增加无正文metadata，确认完整packet为3950码点，Memory1/History2/Study2，包含教材页与错题。该次模型却将引用ID缩写，未满足固定合同的精确引源，因此答案引源FAIL、整体仍PARTIAL。首次运行observer未按域记录，不由后一次补造第一次的元数据。两份原始输出/assessment、旧observer及变更hash都保留；官方catalog超时后使用bundled catalog的stderr也保留。没有改模型输出或降低验收标准。

WorkBuddy当前安装5.7.3的command/HTTP执行器尚未找到从session取消到请求的完整传播，单改常驻连接不能关闭此门槛。官方5.9.3安装包已只读下载并核验Tencent有效签名和产品版本，正在静态核对新版合同；未执行安装器、升级现有客户端或据更新日志声称修复。当前Desktop、高级原生矩阵与生产仍各自未完成，不发布正式版。

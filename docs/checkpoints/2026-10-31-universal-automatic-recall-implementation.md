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
- `PARTIAL`：Hermes 真实插件装载、原生只读 MCP 与生命周期组件已有隔离命中及上下文交付回执；没有模型答案使用。其他三端真实自动回调、客户端进程重启及生产启用仍 `NOT RUN`。

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
| Codex | CLI `0.166.0-alpha.5.1`、hooks stable/true；当前聊天原生 workflow/health 成功 | 自动提问 hook 均未运行；MCP 连通不是自动触发 | `AGENT_DIRECTED`；Desktop 自动验收 `NOT RUN` |
| WorkBuddy | 安装程序 `5.7.3` 与 bundled CLI 合同 | 静态回调/环境身份已核实；实际桌面链路未运行 | `AGENT_DIRECTED` 兼容路径；自动验收 `NOT RUN` |
| Hermes | 独立官方 `v0.22.5` / `bf0203a794a0`，原生只读 MCP 已连接 | 真实 loader/client/lifecycle 组件交付命中证据；普通提问 CLI 返回认证缺失 | 组件 `PARTIAL`，模型验收 `AUTH_REQUIRED`；不是 `AUTO` |
| DSH | EXE/包 `0.2.1-rc.3`，原生工具 registry 合同 | native-mode 路径可实现；PTC 无已验证调用方案；根 CLI 版本启动超时 | 自动验收 `NOT RUN`，PTC `UNSUPPORTED` |

## 数据与发布边界

没有检索生产个人资料、读取私人 store、写生产数据、自动采集、变更生产配置、停用既有插件或推送发布。隔离测试准备和真实 Host 运行将分别记录；不能把本文件的合成回归写成生产效果或四端 `AUTO`。

### 用户截图后调整认证入口

用户截图与当前授权页明确显示账号未启用设备代码登录，继续按钮被禁用；不能把用户已打开网页误判为登录成功。旧 device flow 最终实际超时 15 分钟，原码不再使用。核实官方认证文档以及本次锁定 Hermes 的 browser 参数后，改用独立浏览器 OAuth/PKCE、localhost callback，不更改账号设备代码安全设置、不复制既有凭据。新内置浏览器页面已实际到达“欢迎回来”登录表单，仍需用户本人完成账号认证；回调成功前保持 AUTH_REQUIRED。独立启动器的 Login 入口同步改为 --browser。来源：[OpenAI 官方认证说明](https://learn.chatgpt.com/docs/auth?surface=cli)。

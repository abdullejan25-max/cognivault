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
- `PARTIAL`：正式只读连接、command hook、Codex 持久宿主适配、DSH 原生桥接及 Hermes 通用接口正在实现/验证；不代表实际宿主加载。
- `NOT RUN`：四端真实自动回忆回调、正式隔离命中、模型答案使用、真实进程重启及生产启用。

## 设计修正与尚存限制

1. 多词空格拼接和追加“相关记录”与 Gateway 字面匹配不适配；已要求每域单个短主题/谓词，用真实隔离 Gateway 用例验证命中，而非只有总能返回结果的 callback。
2. command hook 不自动持有宿主原生 MCP 句柄；独立连接必须事先正式只读配置，不能在原生不可用时偷偷 fallback。
3. Hermes 优先使用通用 `pre_llm_call`，不抢占唯一外部 memory provider，也不启用同步/提炼/镜像写入。有限位置未找到有效本机入口，当前版本 `UNKNOWN`；失效快捷方式不算安装成功。
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

## 逐端状态（实施中）

| 客户端 | 已核实版本/连接 | 触发/计划/命中/交付/答案 | 当前真实状态 |
| --- | --- | --- | --- |
| Codex | CLI `0.166.0-alpha.5.1`、hooks stable/true；当前聊天原生 workflow/health 成功 | 自动提问 hook 均未运行；MCP 连通不是自动触发 | `AGENT_DIRECTED`；Desktop 自动验收 `NOT RUN` |
| WorkBuddy | 安装程序 `5.7.3` 与 bundled CLI 合同 | 静态回调/环境身份已核实；实际桌面链路未运行 | `AGENT_DIRECTED` 兼容路径；自动验收 `NOT RUN` |
| Hermes | 有效入口未定位，版本 `UNKNOWN` | 官方通用插件合同；本机生命周期与 native MCP 未核实 | 自动接入 `UNSUPPORTED`，原生验收 `BLOCKED` |
| DSH | EXE/包 `0.2.1-rc.3`，原生工具 registry 合同 | native-mode 路径可实现；PTC 无已验证调用方案；根 CLI 版本启动超时 | 自动验收 `NOT RUN`，PTC `UNSUPPORTED` |

## 数据与发布边界

没有检索生产个人资料、读取私人 store、写生产数据、自动采集、变更生产配置、停用既有插件或推送发布。隔离测试准备和真实 Host 运行将分别记录；不能把本文件的合成回归写成生产效果或四端 `AUTO`。

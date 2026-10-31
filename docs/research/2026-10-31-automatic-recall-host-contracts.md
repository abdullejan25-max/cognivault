# DSH / Hermes 本机扩展合同只读调查

日期：2026-10-31。范围：本机安装程序与公开官方文档；不实现适配器、不变更用户配置、不读取个人会话、数据库、store、Memory 文件或凭据，不启动 MCP / Gateway 直连。WorkBuddy 与 Codex 由主代理单独调查。

## 结论与验收层级

| 客户端 | 本机版本证据 | 作答前合同 | 当前限制 | 本次结果 |
| --- | --- | --- | --- | --- |
| DSH | EXE FileVersion `0.2.1-rc.3`；核心包均为 `0.2.1-rc.3`；安装注册表一致 | 已核实 `agent/pre-step`、不可变消息构造、`ctx.tools.execute` 到原生 MCP 桥 | 工具 `ptc` 呈现模式会拒绝普通插件直接执行非 `run_code` 工具；正式配置/真实回调未验收 | 程序合同调查 DONE；CLI 根版本执行 TIMEOUT；真实 Host NOT RUN |
| Hermes | 当前有效安装入口/版本未找到；历史文档与失效快捷方式不能证明已安装 | 当前官方通用插件有 `pre_llm_call`；比占用外部 memory provider 更贴合只读召回 | 本机代码、插件加载、原生 MCP 执行 API、取消均未确认 | 本机定位 PARTIAL；真实 Host BLOCKED / NOT RUN |

不能从上述静态合同宣称任何端已经具备 `AUTO`。本轮没有实际 Gateway 调用、召回证据、模型回答或生产运行验收。

## DSH 安装与证据定位

实际安装：`%USERPROFILE%\AppData\Local\Programs\DeepSeek Harness\DeepSeek Harness.exe`。

- `FileVersion=0.2.1-rc.3`、`ProductVersion=0.2.1.0`；卸载注册表 `DisplayName=DeepSeek Harness 0.2.1-rc.3`、`DisplayVersion=0.2.1-rc.3`。
- 程序归档：同目录 `resources\app.asar`，182434783 字节；SHA256：`04B32E3A872826BA15CFB2081C6A929A8D4ECAA65E09A2543E5A6B30E2EFF1F0`。
- 使用 Python 只读解析 ASAR 索引、将其中 `@deepseek-ai` 程序 JS / package JSON / README 解出到 Git 外临时目录 `%USERPROFILE%\AppData\Local\Temp\cognivault-host-contract-audit\dsh`。未读取 DSH 用户 profile、配置或会话；未执行解出的程序模块。
- 以下 `包名/相对文件:行号` 均指该归档内 `dsh/node_modules/@deepseek-ai/` 下文件；临时解出副本可按同一路径复核，行号来自实际安装资源而非网上 main。
- `dsh-agent`、`dsh-agent-loop`、`dsh-mcp-client`、`dsh-tools`、`dsh-desktop-host`、`dsh-plugin-manager` 的 `package.json:4` 均为 `0.2.1-rc.3`；`cordis-plugin-loader` 为 `1.0.6`。服务名是 `agents`，包名是 `dsh-agent`，本安装没有名为 `dsh-agents` 的包。
- 包元数据标注源仓库：[deepseek-ai/deepseek-harness](https://github.com/deepseek-ai/deepseek-harness)。此 URL 只表示包的 repository metadata，本报告以本机归档为版本合同。

### 作答前事件与真实用户输入

`dsh-tool-cordis/lib/types/api-catalog.js:4004-4009` 给出确切合同：

```ts
'agent/pre-step'(
  this: Scoped<Agent>,
  payload: {
    agent: Agent;
    messages: UserMessage[];
    turn: number;
    step: number;
    signal: AbortSignal;
  },
  next: () => Promise<PreStepDecision>
): Promise<PreStepDecision>
```

`dsh-agent-loop/lib/index.js:902-926` 在首次模型请求前等待该 waterfall。`next()` 的默认结果是 `{kind:'enter', messages:[...claimed, runtimeContext?]}`；监听器应先等待 `next()`、保留 `reject` 与 `signal.aborted`，再附加证据，不能忽略下游门禁。`dsh-agent/lib/types/dispatch.js:31-42,68-71` 确认 dispatcher 注入同一 scope 对应的 `agent`。`Agent.ctx` 是 agent 独立注册域（`dsh-agent/README.md:61-63`）。

`messages` 不全是真实用户输入：runtime context 与其他插件同样使用 user-role。过滤至少依赖 `message.source.kind==='user'`，再取文本 content parts；不能用 `role==='user'` 区分授权输入与召回证据。

### 消息身份、turn 与取消

- `turn` 是数值，不存在已证明的 `turn.id`。`agent-loop/lib/index.js:943-959` 增加 turn 并在每 step 发 pre-step。
- `dsh-llm/lib/index.js:35-41,59-63` 的 `createUserMessage()` 经 `createMessage()` 生成不可变 UUID `message.id`。普通桌面 RPC 输入还携带 `source.rpcId=request.requestId`（`dsh-api-session-controller/lib/index.js:3218-3230`）。
- Core `request_id` 可由 `agent.session.id + 当前真实用户 message.id` 构成，RPC `source.rpcId` 可用于额外关联。只使用 `(session,turn)` 不够：`agent-loop:806-813` 区分 `followup → next-turn` 与 `steer → next-step`，同一 turn 后续 step 可以接收新的用户输入。每个新真用户 message 都要重新召回；同一 message 重入去重。一个 batch 含多条用户消息时，需要确定性组合其 ID，并明确该批次的当前问题语义。
- `agent-loop:815-820` 的 `agent.cancel()` 只 abort 当前 agent phase 的 controller；pre-step 收到此 `signal`。不要调用全局服务 dispose 或停止 Gateway 来取消一条召回。
- 自设 3 秒 deadline 应与该 turn signal 合并，并同时传播至子进程与 Host 工具执行；只用 `Promise.race` 丢弃返回值不等于取消已开始的调用。该实现尚未运行验证。

### 原生 MCP 调用的确切 API

本安装没有已证明的 `ctx.mcp.callTool` 或 `ctx.mcp.call` 服务。`dsh-mcp-client` 将已配置 server 的工具注册到 `ctx.tools`，每个实例连接一个 server。

- `dsh-mcp-client/lib/index.js:56-60,96-101`：公开工具名为 `mcp__<serverName>__<rawName>`，超长/不合法字符会正规化并加 hash；不能反向拆公开名称恢复 wire name。
- 同文件 `127-154`：发现工具，`ctx.tools.register(definition)`；内部真正调用 `client.callTool({name:tool.name,arguments:args},{signal:execution.signal,timeout:opts.toolCallTimeoutMs,toolDefinition:tool})`。
- `dsh-tools/lib/index.js:3198-3213`，以及 `dsh-tool-cordis/lib/types/api-catalog.js:3394-3397,8175-8188`：插件应经下列 Host 执行入口，保留策略/guard/超时与结果归一化：

```ts
await ctx.tools.execute({
  callId,                       // 适配器产生的本次调用标识
  name: 'mcp__cognivault__retrieve_evidence',
  arguments: { queries, limit },
  agent,                        // 必须保留调用 agent，不能转全局绕过限制
  signal: combinedAbortSignal
});
// Promise<ToolExecutionResult>
// success: {isError:false,value,content,...}
// failure: {isError:true,error,content,...}
```

该名称仅为 serverName 恰为 `cognivault` 且 rawName 为 `retrieve_evidence` 时的公式示例，未查用户配置，未证明本机已注册。执行前应按 agent 可见 registry 核实工具存在及 schema，而不是另起 MCP client。MCP bridge 的成功 `value` 是 `{content, structuredContent?}`（`dsh-mcp-client:226-253`），须解包此层并保留 MCP 错误状态，不能假设 `result.value` 就是 CogniVault DTO。

**PTC 限制必须先解决。** `dsh-tools:3095-3111,3180-3196,3227-3281` 显示：agent 呈现模式为 `ptc` 时，无 `parent` token 的工具调用只允许 `run_code`，直接调用任意 MCP 工具会返回 `UNKNOWN_TOOL`。`parent` 是真实执行 token，不能伪造；不能改为 `ctx.tools.get(...).execute(...)` 跳过策略，也不能省略 `agent` 改查全局 registry。上述 `ctx.tools.execute` 合同在 native 模式有明确静态路径；对当前用户 agent 是否为 native、本机是否存在正式的 Host 内部调度通道，本轮未读配置、未验收。适配器必须检测并报告这一模式缺口；不能宣称四端通用 callback 已保证 DSH PTC 可执行。

资源通道另为 `ctx.mcpResources`，不是工具调用入口。`dsh-mcp-client:685-698,731-741` 仅注册 `resources/list`、`resources/templates/list`、`resources/read` 以及 server instructions，不能拿它调用 `retrieve_evidence`。

### 证据注入与插件装载

本机参考实现 `dsh-time-context/lib/index.js:215-251`：监听 pre-step，等待 `next()`，返回：

```ts
{
  ...decision,
  messages: [...decision.messages, createUserMessage({
    content: [{type:'text', text: boundedUntrustedEvidenceEnvelope}],
    source: {kind:'cognivault-automatic-recall'}
  })]
}
```

`createUserMessage` 从 `@deepseek-ai/dsh-llm` 导入；应使用完整工厂构造，不是裸 `{role,content}`。固定外框与结构化 `untrusted_data` 内容仍须恶意来源用例证明，user-role/source 标签本身不提供模型安全保证。

`dsh-agent-loop:1067-1084` 把决策消息写入 `session.append('user/message',...)` 后再构造和执行 LLM 请求。因此注入证据进入 Host 自身会话日志，不能宣称只有内存传输或不会落盘。适配器诊断日志不记录正文，与 Host 正常保存模型上下文是两件事；应在启用前明确披露这一行为。

`cordis-plugin-loader/lib/index.js:213-227,450-464` 按条目 name 导入模块，再 `unwrapExports`，并通过 `ctx.registry.plugin(plugin,options.config,...)` 挂载。`dsh-time-context:102,112,157` 展示原生插件的 `name`、`inject`、`apply(ctx,config)`。本轮只确认加载机制，未读取/写入用户 `cordis.yml`，未安装插件、未核查用户既有记忆插件状态。

### CLI 版本执行结果

`dsh-desktop-host/lib/cli.js:94-115` 是安装 CLI 入口，使用 `ELECTRON_RUN_AS_NODE=1`。`dsh/lib/bin.js:101-132,207-216` 表明根 `--version` 经参数解析退出，在 profile boot / `loadLayeredEnv()` 之前。

本次仅运行一次隐藏子进程：安装 EXE + 上述归档内 CLI 路径 + `--version`，仅对子进程设置 `ELECTRON_RUN_AS_NODE=1`。10 秒未退出，终止的仅是该新建子进程；没有重试、没有启动 profile、没有 Gateway 调用。结果为 `VERSION_TIMEOUT`，不能记作 CLI version PASS。版本结论来自 EXE/注册表/package 三类静态一致证据。

## Hermes 本机定位与官方候选合同

### 有限安装位置检查

本轮检查 PATH、常规 uv/pipx tools 目录、Local Programs、Local/Roaming 一级同名目录、相关日期项目目录的一级条目、标准卸载注册表及 Start Menu 的 Hermes 名称快捷方式。没有全盘扫描、没有递归搜个人文档正文。

- PATH 没有 `hermes`；已知 `~/.hermes`、`~/hermes-agent` 不存在。
- `%USERPROFILE%\AppData\Local\com.nousresearch.hermes.setup` 只有 `EBWebView`；这不是已确认 Hermes CLI 安装。
- 找到 `...\Start Menu\Programs\Hermes.lnk`。通过 WScript.Shell 只读解析，target 为 `%USERPROFILE%\AppData\Local\hermes\hermes-agent\apps\desktop\release\win-unpacked\Hermes.exe`，无 args；当前该 EXE 和此 source root 不存在，因此它是失效安装线索。
- 相关历史目录 `Documents\Codex\2026-09-12\hermes-git` 当前只有 `outputs`、`work`，`work` 下只有 `mem0`；没有读取输出或用户文件。
- 仓库历史 `docs/p12-step3-hermes-checkpoint.md:12-15` 记过 source tree + Python 3.11.15 venv、CLI `v0.20.0 (2026.8.3)`。这是 2026-10-01 的历史事实，不能替代本机当前安装/版本。

结论是“在有限常规位置未定位有效入口，版本 UNKNOWN”，不是“机器绝对没有 Hermes”。因此无法针对当前本机版本给出插件到 native MCP 的已证实 API；不得用在线 main 的内部函数名冒充安装合同。

### 当前官方资料与方案选择

[官方 Event Hooks](https://hermes-agent.nousresearch.com/docs/user-guide/features/hooks#pre_llm_call)（2026-10-31 查阅；页面 `pre_llm_call`、plugin catalog、timeout 章节）：通用插件通过 `ctx.register_hook('pre_llm_call',callback)` 在每轮工具循环前执行；接收 session/user message/history、model/platform 及 hook-specific IDs，回调接受 `**kwargs`。返回字符串或 `{context:str}`，注入当前 user 消息，不是 system。多模态 `user_message` 可以是 content-parts 列表。当前文档列出 `turn_id`，但需本机实测字段存在，不能猜 ID。超时回调可能被弃置，不能把 Host timeout 当成召回取消；注入内容可能由 Host 会话保存。

[官方通用插件开发](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins) 描述 native `plugin.yaml` + `register(ctx)` 和启用机制；通用 hook 不要求注册外部 memory provider。只注册作答前回调、不注册 post-response/session-end 写入回调，可以保持插件自身只读；不代表 Hermes 现有其他插件/内置 Memory 自动失效，也不授权关闭它们。

[官方 Memory Providers](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory-providers#how-it-works) 说明仅允许一个外部 provider，生命周期还包含对话同步、session-end 提取和内置 Memory 写入镜像。因此本轮只读召回优先采用通用 `pre_llm_call` 插件，不占 provider slot、不启用采集生命周期。自定义 provider 理论上可实现无操作写入，但生命周期与共存成本更高，当前无理由把它作为首选。

正式实现前仍需：定位/恢复明确的 Hermes 安装，核对版本与实际 plugin manager，证明原生 MCP 调用入口、真实 turn/request ID 与可隔离取消；配置恢复与真实 Host 验收单列。不得为跑通测试自动安装/升级、读凭据、直连 Gateway 或创建临时 override。

## 实现与真实验收的明确边界

1. 可以依据以上静态证据开发 DSH native-mode 适配合同及合成测试；Python core 子进程只发送计划请求，由 DSH Host 自身执行已配置原生工具并返还结果，子进程不得自行连接 Gateway。
2. DSH PTC 模式及 Hermes 本机缺入口是实质未完成项，应 fail-open 为普通回答并清楚记录 `UNAVAILABLE` / 接入 `UNSUPPORTED`，不得伪造 `AUTO`。
3. 必须另做新独立真实 Host 会话：首次作答前回调、正式隔离 Gateway 调用、模型收到证据、来源引用、mid-turn steer、重复送达、取消隔离、恶意来源与进程重启。本轮这些都是 NOT RUN。
4. 本报告只新增调查 Markdown；安装程序、用户配置、会话、资料库均未修改。


## Codex 与 WorkBuddy 补充调查

本节同日只读检查，属于程序/文档合同证据，不是回调或模型验收。

### Codex

- 当前 PATH 的 `codex --version` 返回 `0.166.0-alpha.5.1`；`features list` 显示 `hooks stable true`。
- [官方 Hooks](https://learn.chatgpt.com/docs/hooks) 已实际打开：`UserPromptSubmit` 输入含 `session_id`、`turn_id`、`prompt`；同步 `hookSpecificOutput.additionalContext` 在当前模型回答前交付。宿主可能保存其正常会话内容；本插件诊断不写正文，不代表宿主不保存。
- 官方 `mcp_tool` hook 只能调用已连接工具，`${field}` 是字段模板展开，不执行共用规则规划，也不提供 command hook 复用原生 MCP 的 RPC 句柄。把完整 prompt 直接映射到一个连续子串查询不满足本设计。
- 本次已通过当前聊天原生入口读取 `study-workflow://personal-answer`，并成功 `health_report`（`ok=true`，响应中的 `gateway_version=0.8.0`）。版本字段只是实际响应，不能据此推断安装包/当前源码版本一致。没有检索生产个人内容。
- 指定工作树 `.codex/config.toml` 和 `config.local.toml` 均不存在；当前原生连接成功说明当前聊天可用，不证明此工作树独立配置已加载、受信任或 hook 已执行。不得自动复制主目录私有配置、创建临时 override 或用独立客户端补成 Desktop PASS。

### WorkBuddy 5.7.3

- 卸载注册信息和 EXE `FileVersion=5.7.3` 一致；安装程序 `resources/app.asar/package.json` 为 `@genie/workbuddy-desktop`、version `5.7.3`。只读解析归档，必要程序副本保存于 Git 忽略的本计划临时目录。
- 归档内 `cli/dist/codebuddy-lite-wb.mjs:2393` 的 `executeUserPromptSubmitHooks` 在普通用户输入后调用 hooks，并将 `hookSpecificOutput.additionalContext` 放入当前 session 的 `hookContexts.userPromptSubmitContext`。
- 同文件 `:878` 在 user 消息交付模型前消费该字段，包装为 `<system-reminder data-role="hook">`。JSON 中所有来源字段（包括 metadata 与 keys）的 `<`、`>`、`&` 都应进行 JSON Unicode 转义；这只能防止来源闭合外框，不能证明模型抵抗注入。
- stdin 的序列化输入在 `:968` 的 command executor 剔除 `session` 对象。`:972` 的公开 SDK 转换仅有 session/prompt，不能假设有 `turn_id`。
- 但 `:1794` 的模块 `40365`、`buildChildContextEnv({session})` 明确从 `session.conversationRequestId` 注入子进程环境 `CODEBUDDY_CONVERSATION_REQUEST_ID`；`:2353` 的 `addHistory` 在调用提问 hook 前为真实外部用户输入生成并设置该 ID。适配器可使用这个原生环境字段作为 request identity，缺失时必须返回 `UNAVAILABLE`，不按 prompt 哈希猜轮次、不读 transcript。
- command executor 会输出宿主自有 debug 日志；本实现不额外记录原始问题/证据，不能承诺 WorkBuddy 自身日志不保存用户问题。
- 程序代码证明候选路径存在，不证明当前桌面真的装载/启用此 hook。当前没有运行 WorkBuddy 的桌面回调、原生 Gateway 命中、答案使用或重启验收。

### 明确的设计修正

1. Hermes 优先用只读通用插件 `pre_llm_call`，不注册单一外部 memory provider，不影响已有提供器。
2. DSH 以真实用户消息 ID 去重，不能仅凭数值 turn；PTC 模式保持未支持，不绕过工具策略。
3. Codex/WorkBuddy command hook 如无原生调用句柄，需要独立、持久、显式只读的正式 MCP 连接配置。它是单独的接入路径，不是原生 Desktop MCP 成功的替代证据；配置缺失时不能自行启动直连 fallback。
4. 查询改写必须适应 Gateway 字面匹配：每域使用单个主题/谓词短查询，不能用空格拼合多个不连续词或追加“相关记录”来制造无命中的查询。

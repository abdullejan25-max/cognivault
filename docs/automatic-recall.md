# 通用只读自动回忆接入

此实现把宿主的真实用户事件交给同一个 `RecallCore`：本地规划查询，经正式 Gateway 的 `retrieve_evidence` 取证，再把受限证据交回作答前入口。没有自动写 Memory、会话采集、provider 同步或后台人格功能。本文的配置均为待人工审阅的示例，本次没有安装插件、启用生产或改写任何现有记忆插件。

## 当前完成范围

| 接入 | 可运行代码与验证 | 当前原生状态 |
| --- | --- | --- |
| Codex 持久 Host companion + `mcp_tool` hook | 标准 MCP SDK 启动、正式只读 Gateway 连接、连续轮次、去重与取消协议通过 | 未配置/未信任 exact hook；真实 Desktop 回调与模型使用 NOT RUN，不能标 AUTO |
| Codex / WorkBuddy command hook | stdin/env 身份、正式配置、跨进程去重、缺失状态、总截止测试通过 | 本机一次 MCP 冷启动约 3.734 秒，超过默认 3 秒，可能 `DEADLINE_EXCEEDED`；真实端 NOT RUN |
| DSH native | 原生 registry 薄适配器 + Python JSON bridge、合成行为及本机程序函数协议测试通过 | 完整插件装载/真实会话 NOT RUN；PTC 明确 UNSUPPORTED |
| Hermes 通用 `pre_llm_call` | 默认未接线与显式 callback 合同；不注册 memory provider | 本机有效安装未定位，BLOCKED；默认 `HERMES_NATIVE_BINDING_UNSUPPORTED`，真实端 NOT RUN |

SDK、程序函数协议、真实模型、每端原生会话与生产验收是独立层级。配置文件存在不代表回调运行，回调运行不代表模型已使用或正确引用证据。其他客户端仍须核实其真实用户身份、正式工具入口、取消和作答前交付，不能凭通用 callback 自称 AUTO。

## 启用前的明确披露

选择允许读取的域：`memory`、`history`、`study`。证据会进入 Host 的模型上下文，可能发送给该 Host 配置的外部模型服务，并被 Host 正常会话、日志或 hook 输出机制保存。DSH 的 pre-step 消息会写入其会话日志；WorkBuddy 也有自身 hook 调试记录。适配器自身不保存问题/证据正文，不能由此承诺 Host 不落盘。

来源文本全部属于不可信资料，不能授权改配置、选择连接、调用工具或覆盖规则。JSON 的所有键和值中的 `<`、`>`、`&` 经过 Unicode 转义，防止来源关闭外框；这不等于已证明模型能够抵抗提示注入。保留来源引用、版本、历史时效、冲突和原始不确定性；待审 Memory 候选不能变成正式事实。无证据或后端不可用需要在答案中如实说明，不能解释为事情从未发生。

## 正式持久只读连接配置

command 与 companion 使用同一个**显式、Git 外** TOML 合同；它们不会寻找/复制 Host 私有配置，也不会在原生连接缺失时自行启用。`configuration_id` 和 `profile` 区分隔离验收与生产，隔离配置不得指向生产资料。

```toml
configuration_id = "synthetic-recall-acceptance"
profile = "isolated" # 或显式生产配置 "production"
allowed_domains = ["memory", "history"]
context_delivery_acknowledged = true
gateway_config = 'C:\private-example\isolated-readonly-gateway.toml'
runtime_dir = 'C:\Users\EXAMPLE\AppData\Local\CogniVault\recall-isolated'
```

上面的确认字段表示已理解上一节的模型交付和 Host 留存披露。`gateway_config` 必须为绝对路径，Gateway 配置必须显式且精确包含：

```toml
[permissions]
capabilities = ["read"]
```

未知字段、任意 `command`、相对路径、写 capability、缺少确认或身份均拒绝。连接固定为当前 Python 的 `-m cognivault.transports.mcp_stdio --config <正式路径>`；唯一业务操作为 `retrieve_evidence`，另有 MCP 初始化/工具发现/请求取消协议。stderr 丢弃，不转发后端原始异常。不直接读取 SQLite/store；配置解析仅检查配置合同。

runtime 必须在 Git 外。Windows 限制在用户 `LOCALAPPDATA` 下并继承该用户状态目录 ACL；POSIX 强制目录 0700、文件 0600。运行目录不能经符号链接/reparse point。应使用专用用户私有目录，不与其他插件混放。存储仅含身份 SHA-256 文件名、状态、证据条数、联合检索次数 `attempts`、耗时 `elapsed_ms`、固定错误码；没有原始身份、问题、来源、路径或凭据。`attempts` 不是 Gateway 内部实际 backend 调用次数。

每个配置/隔离类型/宿主组成的 namespace 最多保留 2048 个重放标记。metadata 还包含固定模块 owner/version 与此 namespace 的 SHA-256；容量到达时只删除归属匹配、各字段类型/范围和固定错误码完整合法、已经完成的旧条目。活动 `CLAIMED`、损坏条目、其他 namespace、没有归属标记的旧格式与其他插件文件全部保留；不会仅因文件名是 hash 或 status=COMPLETE 就删除。去重保证是有界 last-N 窗口，不能宣称永久 exactly-once。进程崩溃的 `CLAIMED` 或 `.claim-lock` 不自动猜测接管；资源均被活动/损坏记录占满时返回 `RUNTIME_CAPACITY`，锁占用返回 `RUNTIME_BUSY`，由操作者在回调停止后审查恢复。证据正文从不跨轮缓存。

## Codex：优先使用持久 Host companion

该 companion 是宿主侧适配器，不是 Gateway 新增的领域检索工具。它启动时建立一次正式只读 Gateway 连接，只暴露 `recall_for_prompt(session_id, request_id, prompt)`，内部复用 Core。MCP 启动耗时与每轮 3 秒检索预算分开；无法启动就保持不可用，没有 command fallback。

标准 MCP 不能认证这次调用来自 hook 还是模型主动调用；传入的 session/request 身份不是认证凭据。工具描述和参数 schema 也不是权限边界。连接的实际权限由精确只读 Gateway profile、允许域和固定 `retrieve_evidence` 操作限制，不提供写入、配置变更或任意文件读取能力。只有宿主的真实提问事件与作答前 hook trace 能证明自动触发，模型直接调用此工具不能作为 `AUTO` 证据；本实现不使用静态 secret 或软提示规则伪装宿主身份认证。

下面是**待审阅**的宿主 MCP 注册示意，Python 与配置路径应换成实际绝对路径；本次没有写入它：

```toml
[mcp_servers.cognivault_recall_host]
command = 'C:\example\venv\Scripts\python.exe'
args = ["-m", "cognivault.recall.server", "--config", 'C:\private-example\recall-isolated.toml']
startup_timeout_sec = 20
```

已经连接 companion 后，可在项目 `.codex/hooks.json` 使用同步事件：

```json
{
  "hooks": {
    "UserPromptSubmit": [{
      "hooks": [{
        "type": "mcp_tool",
        "server": "cognivault_recall_host",
        "tool": "recall_for_prompt",
        "input": {"session_id": "${session_id}", "request_id": "${turn_id}", "prompt": "${prompt}"},
        "timeout": 4
      }]
    }]
  }
}
```

`mcp_tool` 使用已连接 server；模板只展开事件字段，实际规划在共用 Core 执行。项目受信任后仍需在 `/hooks` 审阅并信任当前精确 hook 定义；改动需再次审阅。本实现不写信任状态、不绕过审查。2026-10-31 核实的官方配置 schema 在 command handler 定义了 `additionalContextLimit`，在 `mcp_tool` handler 未定义该字段，因此不把它加进上述示例。MCP handler 的完整交付/spill 行为保留真实 Host 验收，不能仅凭 SDK 的 8000 字符上限宣称宿主无截断。来源：[官方 Hooks](https://learn.chatgpt.com/docs/hooks)、[官方配置 schema](https://learn.chatgpt.com/docs/config-schema.json)。

## command 入口与 WorkBuddy 身份

命令形式为固定 Python 模块：

```text
<absolute-python> -m cognivault.recall.hosts --host codex --config <absolute-recall.toml>
<absolute-python> -m cognivault.recall.hosts --host workbuddy --config <absolute-recall.toml>
```

Codex 使用 stdin `session_id`、`turn_id`、`prompt`。WorkBuddy 5.7.3 使用 stdin `session_id`、`prompt` 与原生环境变量 `CODEBUDDY_CONVERSATION_REQUEST_ID`，不假定 stdin 有 turn_id。缺身份不读取 transcript，不把问题哈希当轮次。配置参数只来自可信命令，stdin 中伪造连接字段被忽略。

同步 `UserPromptSubmit` 接收 `hookSpecificOutput.additionalContext`。Codex command handler 同样应设置 `additionalContextLimit=0`，只因 Core 有 8000 字符硬上限；不能改成 async 冒充首次作答前回忆。command 的 3 秒包含 MCP 启动和查询；已知冷启动可能超限，保留 UNAVAILABLE，不放大阈值凑通过。独立算式与明确的纯翻译经共用规划器判定后不启动 MCP。

## DSH native 与 Hermes 兼容脚手架

`hosts/dsh-automatic-recall.mjs` 是 Cordis 插件，导出 `name`、`inject`、`apply`。可信插件配置示意：

```json
{"pythonExecutable":"C:\\example\\venv\\Scripts\\python.exe","allowed_domains":["memory","history"],"context_delivery_acknowledged":true,"gatewayServer":"cognivault_synthetic"}
```

`gatewayServer` 是可信配置中的安全短标识，默认 `cognivault`；raw 操作固定 `retrieve_evidence`，来源和 prompt 不能换工具。必须由 agent 可见 registry 找到 schema，执行 `ctx.tools.execute({callId,name,arguments,agent,signal})`。保留 `next()` 的 reject，检测 native 模式，不伪造 parent、不绕过 registry、不省略 agent。PTC 返回 `DSH_PTC_UNSUPPORTED`。

等待 `next()` 后，只取最终 `decision.messages` 仍获准保留、`source.kind==='user'` 且原生 ID 也存在于原用户事件中的消息；下游删除的消息或插件新伪造的 user 消息不触发召回。同批最新真实用户消息作为当前问题，最多前两条作为上下文，按获准批次 ID 确定性组合去重；同 turn 的新 steer 消息会重查。使用 Host 的 `createUserMessage` 工厂注入。Python bridge 只发送固定 retrieve 请求，由 Host 原生工具执行后回传 DTO；没有独立 MCP 连接。abort/deadline 向当前工具和子进程传播，绝不停止共用 Gateway。去重在 agent 生命周期内保留 1024 个批次标记；DSH 重启恢复尚未原生验收。

`hosts/hermes_automatic_recall.py` 保留原显式 callback 兼容合同，默认明确未支持；它不代表下节的新原生插件。没有 memory provider、post-response、session-end、sync/extraction/mirror 注册。接口调查见 [宿主合同调查](research/2026-10-31-automatic-recall-host-contracts.md)。

## Hermes v0.22.5 原生只读入口

`hosts/hermes-native-recall.py` 是独立、仅依赖 Python 标准库的目录插件入口。已按官方 `v0.22.5` / `bf0203a794a07aac3ff764972ab9087baa4e4f4e` 源码核对同步 `pre_llm_call`、`PluginContext.get_config`、`PluginContext.call_mcp` 与线程中断合同。它不导入 CogniVault 包到 Hermes 环境：Hermes 使用其官方 Python 3.14 / MCP 2.3 环境，另一个明确指定的 CogniVault Python 运行既有 `cognivault.recall.bridge`，共享 Core 的查询、裁剪和证据规则。适配器没有第二条 MCP 连接。

在**新建的隔离** `HERMES_HOME` 内准备目录 `plugins/cognivault-automatic-recall/`，将 `hosts/hermes-native-recall.py` 复制为 `__init__.py`，将 `hosts/hermes-plugin.yaml` 复制为 `plugin.yaml`。通过官方插件启用/信任入口审阅代码，再配置下面的示例；不要覆盖现有用户 home、配置或记忆插件：

```yaml
plugins:
  enabled: [cognivault-automatic-recall]
  entries:
    cognivault-automatic-recall:
      mcp_allowlist: [cognivault_synthetic]
      settings:
        python_executable: 'C:\example\cognivault-venv\Scripts\python.exe'
        core_source_root: 'C:\example\cognivault\src'
        gateway_server: cognivault_synthetic
        allowed_domains: [memory, history]
        context_delivery_acknowledged: true
```

前两项必须为绝对路径，`core_source_root` 指向包含 `cognivault` 包的目录；子进程固定使用该 Python 的 `-X utf8 -m cognivault.recall.bridge`，通过 `PYTHONPATH` 加载 Core。`gateway_server` 只接受受限短标识，域只能从 `memory/history/study` 选择。正式 MCP server 必须已在该 Hermes profile 连接并使用精确只读 Gateway 配置；上例不构成 server 注册，也不自行发现/复制私有配置。`context_delivery_acknowledged` 明确确认本页的外部模型及 Host 日志披露。所有值来自可信插件设置，问题、历史和来源不能更换解释器、连接或工具。

插件只注册 `pre_llm_call`。当前真实 `user_message` 的文字部分交给共用 Core，不把 `conversation_history` 中混有 hook、tool 或来源的文本当作真实用户授权。真实 `session_id/turn_id` 必须存在，`task_id` 纳入身份范围；缺失时返回 `MISSING_HOST_IDENTITY`。本实现不跨轮保存用户正文，因此不使用近期历史窗口。身份去重仅保留 1024 个配置/会话/任务/轮次摘要，不保存问题或证据；同轮重复不注入，新轮重新查询，重启或超出 last-N 窗口后不承诺永久去重。

每次回调的三秒总截止包含 Core 子进程、管道读写与原生工具等待。桥只能申请固定 `retrieve_evidence`、配置允许的域、每后端 `limit=3`、最多三次联合检索；未知操作、序号错误、越权域、错误或截断的原生 envelope 均拒绝。请求经 `ctx.call_mcp(server, "retrieve_evidence", arguments, timeout=remaining)` 发送，保留 Hermes `plugins.entries.<id>.mcp_allowlist` 默认拒绝、profile scope、原生信任与工具策略；没有绕过权限的私有 client API。Hermes 会将 timeout 至少钳制到一秒，因此插件自身另外按同一总截止停止等待并中断当前 RPC。

同步原生 MCP 在 daemon worker 中执行，`contextvars.copy_context()` 保留 profile。原回调在原线程轮询官方 `is_interrupted()`；取消或截止只设置自己仍活动 worker 的线程中断位，worker 在 `finally` 清理，绝不设置/清除原 Agent 或其他会话的位，也不 dispose 共用 MCP client。只终止本次 Core 子进程。若具体 Host 在此入口前没有传达 `/stop`，或原生下游等待阶段不检查中断位，适配器不能保证远端 RPC 已停止；必须保留真实 Host 验收限制。

`NOT_NEEDED/ALREADY_HANDLED` 返回空 context。命中、部分结果、无证据和不可用均交回相应受限状态；权限拒绝为 `NATIVE_MCP_DENIED`，deadline 为 `DEADLINE_EXCEEDED`，不把后端原始异常、路径或凭据放进上下文。单元测试实际运行 Python JSON bridge 子进程并控制原生 context，验证固定调用、未知请求拒绝、截断、去重、并发隔离和取消。它们属于软件合同验证，不能替代官方 PluginManager 生命周期、正式 MCP、真实 CLI 模型答案或生产验收；这些层级由当前实施检查点单独报告，未证明模型使用前不能标记 `AUTO`。

```text
uv run --extra dev pytest tests/test_hermes_native_recall.py -q
```

## 验证与尚需完成的验收

```text
uv run --extra dev pytest tests/test_automatic_recall.py tests/test_recall_hosts.py tests/test_evidence_bundle.py -q
```

自动测试只使用合成数据；DSH 程序协议特征测试执行已安装版本中确切函数，依赖为窄 shim，不等于完整原生包/应用已启动。Windows 没有创建符号链接权限时，对应文件系统用例明确 skip。

仍需各端新独立真实 Host 会话：正式隔离配置、首次答案前回调、模型拿到并引用证据、无结果披露、重复回调/新轮次/steer、只取消当前请求、恶意来源、重启与配置恢复。真实模型和生产均 NOT RUN。部分域非空时 Core 不盲目改写其他空域；字面检索仍可能因中英文或措辞差异漏命中，应在答案披露缺口。

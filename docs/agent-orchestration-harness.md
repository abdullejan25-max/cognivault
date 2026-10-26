# DeepSeek Harness：协议调查与受限接入骨架

核实日期：2026-10-24。安装版本：`0.2.0-rc.2`。

## 本轮结论

真实 headless 模型接入为 `BLOCKED`；真实合成模型任务为 `NOT RUN`。本轮没有发送模型请求，没有访问或读取用户凭据、profile、会话或生产 store。不能由安装存在、源码调查或解析器测试推出 `CONNECTED` / `VERIFIED`。

交付的是 **接入骨架和纯协议解析器**，不是可以调用模型的 subprocess adapter：

- `HarnessHeadlessAdapter.descriptor` 声明外部文本/结构化候选能力；注册器初始状态仍为 `CONFIGURED`，这仅代表声明。
- `healthy()` 固定返回 `False`，不启动进程、不探测或复用 Desktop 会话。
- `execute(task, prepared)` 首先再次检查任务合法性及 `external_allowed`，然后以 `HARNESS_TOOL_ISOLATION_UNVERIFIED` 拒绝；不会读取、序列化、日志记录或外传 `prepared`。
- `describe()` 显式返回 `BLOCKED`、`blocked_reason` 和 `execution_enabled=False`，防止仅看到 Engine 的 `AGENT_UNAVAILABLE` 而误判故障原因。
- `parse_headless_result(stdout, task, descriptor)` 是纯函数。它只解析已获得的字节，不能证明这些字节来自真实 Harness，也不能证明进程退出成功。当前没有生产调用方把它当作已验证执行结果。

`descriptor.kinds` 只是候选路由，未证明 Harness 能正确处理对应领域任务。Parser 测试也没有验证领域语义、Gateway 持久化或真实模型行为。

## 本地源码依据

调查仅读取安装自带 `resources/app.asar` 中必要程序代码；这与用户 `.dsh`、runtime 状态和私人学习数据不同。入口路径已记录在 [环境证据](agent-orchestration-environment.md)。

关键文件（相对于 ASAR 根）及本次读到的 SHA-256：

| 文件 | SHA-256 | 结论 |
| --- | --- | --- |
| `dsh/node_modules/@deepseek-ai/dsh-headless/lib/json-stream-BA-F3lfb.js` | `b6ede8da01119f5caf4baa8051ce8bcd8b956b5b163cf58af91d583b80028c0c` | JSONL 投影的实际字段与终结事件 |
| `dsh/node_modules/@deepseek-ai/dsh-agent-loop/lib/index.js` | `00c4814d63f3d1e2754eb09144832d19b30499d6ea444730473f740b587948c2` | `turn` / `step` 从数值计数器加 1 |
| `dsh/node_modules/@deepseek-ai/dsh-base/cordis.patch.yml` | `9c2be64f46a193eff3cca190c26e697e8e8eee25c9b40149dfdb56f8142c252d` | headless 继承的共享 core 默认挂载多种工具 |
| `dsh/node_modules/@deepseek-ai/dsh-tools/lib/index.js` | `40f47709337c3c205d4e09e647f8588f4977e66f6d52f019b3cc7ef81159d84f` | ToolRuntime.Config 只有展示模式和并行数量；没有核实到配置式全局零工具开关 |

同时读取的 `dsh-headless/lib/startup.js` 确认 `--json`、`--session-id` 和独立的 `-` stdin 参数；`lib/index.js` 确认非完成 turn 退出非零、最终 `final` 在总结后输出。`dsh/lib/bin.js` / `dsh-app-boot/lib/index.js` 确认 profile 启动会加载工作目录及 Harness home 的 `.env` 层，所以只清空 API key 环境变量不足以隔离私有配置。

没有用搜索结果或其它版本的协议替代本机程序源码。

## 为什么停止于 BLOCKED

默认 base 挂载 shell、文件读写、skills、子 Agent、workflow、网络搜索/抓取等工具，还包含会读取 instructions 的服务。`DSH_PERMISSION_MODE=read-only` 是文件效果策略，不能证明全部工具和其它私有上下文读取被禁用；`approval: never` 也不能作为安全隔离。

本机 `ToolRuntime.Config` 的 `mode` 只有 `native`、`ptc`、`both`，三者都是工具展示方式。没有找到可直接配置的 `tools: []` 或 `providers` 全局禁用机制。源码存在 `tools.restrict()` / `tools.guard()` 编程 API；这为未来自定义受限 profile 提供调查方向，但不能把未实现、未验收的插件称为已有可用安全接口。

因此本轮未为了得到一次模型回复而启动默认 headless，也没有读取现有认证来绕过隔离。认证状态仍为 `UNKNOWN`，不能伪称已实际观察到 `AUTH_REQUIRED`。没有实现进程启动，也就没有虚构 stdin 外发、stdout/stderr 限流、取消或超时杀子进程的通过证据。

## JSONL 解析边界

实际投影事件为 `session`、`status`（`turn_start` / `step_start` / `step_end` / `turn_end`）、`thinking`、`text`、`tool_call`、`tool_result`、`final`；错误通道还可写 `error`。

`final` 的实际 envelope **仅为** `{"type":"final","text":"..."}`，不自带 `Result`、任务 ID 或成功标记。未来受控执行器需要通过 stdin 要求模型将完整的本项目 `Result` JSON 放进 `final.text`，再精确校验任务 ID、correlation ID、adapter ID/version。不能把任意最后一行或普通回复包装为可信结果。

当前解析器主动只接受一个无工具 turn、一个完整 step、`reason={"kind":"completed"}` 以及唯一终结 `final`。这是收窄的接入策略，不是上游全部合法运行的实现；多 step、其它 reason、截断或新增字段均会拒绝，需要有依据地扩展并测试。工具事件被拒绝是结果验收防线，**不能阻止已经执行的工具副作用**，不能替代执行前隔离。

解析限制：stdout 总量 1,000,000 bytes，最多 4,096 行，UTF-8，拒绝空行、重复 JSON key、NaN/Infinity、错误/工具/未知事件、截断标记、顺序错误、未完成/重复 final，以及 Result 绑定错误。异常只返回固定错误码，不包含原始 payload、reasoning 或错误 message。

## 验证与后续验收

`python -m pytest tests/test_orchestration_harness.py -q`：36 项通过，全部使用自造 JSONL fixture。已先运行缺失模块的失败测试，再实现骨架；后续数值计数器及布尔值混淆测试也先失败再修正。没有 fake subprocess 被报告为真实外部 Agent 调用。

在启用真实 executor 前，必须确认并验证独立无工具 profile、禁止 instructions/skills/私人配置读取、隔离工作目录和 home、凭据显式授权接入及环境白名单、绝对可执行程序和独立 argv、stdin 唯一任务通道、stdout/stderr 有界读取、取消/超时进程树回收，以及非零退出和最终结果校验。然后仅用自造字符串运行最小真实任务，并分别报告连接、模型任务、权限拒绝、超时和取消证据。当前骨架没有提供绕过开关。

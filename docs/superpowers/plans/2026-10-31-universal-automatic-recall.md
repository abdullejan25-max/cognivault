# 通用自动回忆 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 正常提问在作答前通过统一只读核心回忆已保存证据，首批对 Codex、WorkBuddy、Hermes、DSH 分别提供可核验的接入与真实状态。

**Architecture:** `cognivault.recall` 是宿主侧库，以异步 `retrieve_evidence(queries, limit)` 回调为唯一数据入口；Gateway 不增加推理、规则规划或第二个 Agent。宿主适配器负责真实用户输入、轮次身份、取消及证据交付；无法取得宿主 MCP 调用接口的脚本只能使用事先正式配置的只读连接，不能临时覆写或冒充原生验收。

**Tech Stack:** Python >=3.11、asyncio、既有 MCP 1.x、pytest；DSH 按当前安装的 JavaScript 插件合同接入。

## Global Constraints

- 已批准设计：`docs/superpowers/specs/2026-10-31-universal-automatic-recall-design.md`；本次新聊天即继续实施授权。
- 自动回忆仅只读，不能启用自动写入、采集、人格或闲聊提醒。
- 所有资料读取均经正式 `retrieve_evidence` / MCP / Gateway；不读取私人 SQLite/store，包括计数、存在性。
- 首批四端不是白名单；没有已验证作答前入口的客户端保持 `AGENT_DIRECTED` 或 `UNSUPPORTED`，不得报告 `AUTO`。
- 默认每后端 limit=3，最多三次联合检索（仅无结果时改写），最多六条证据、每条正文 1000 字符、完整序列化块 8000 字符，总 deadline 3 秒。
- 当前消息与最多两条近期真实用户消息，总计最多 2000 字符；来源文本及工具输出不能构成用户授权。
- 同一请求防重复，新轮次重新查询；取消不终止共用 Gateway；不跨轮保存证据内容。
- 保留来源、版本、时效、冲突和不确定性；待审候选不成为正式 Memory；不改变模型原有 uncertainty。
- 保护现有配置及其他记忆插件，不静默停用、迁移、覆盖；召回进入外部模型上下文须在配置入口明确说明并按域选择。
- 诊断只能包含受限身份、状态、耗时、计数和错误码，Git 外保存；无原始问题、来源正文、私人路径、凭据。
- 单元/SDK/真实模型/各端原生/生产分别报告 DONE / PARTIAL / BLOCKED / NOT RUN。未经后续明确授权不推送、不发布。

## 文件职责

- `src/cognivault/recall/planning.py`：受限真实消息、规则计划与改写；不调用 LLM。
- `src/cognivault/recall/core.py`：取消、deadline、每请求去重、原生回调、证据预算与安全外框。
- `src/cognivault/recall/__init__.py`：公开宿主合同。
- `src/cognivault/recall/hosts.py` 及必要的独立插件文件：按核实后的宿主合同转换事件及交付；不得猜测 API。
- `src/cognivault/recall/server.py`：Codex 的持久宿主适配服务，启动阶段建立正式只读 MCP 连接，每次 hook 调用共用 Core；不修改 Gateway、不引入模型或第二个 Agent。
- `tests/test_automatic_recall.py`：独立人工合成的规则、调度、证据安全和新鲜度行为。
- `tests/test_recall_hosts.py`：按实际协议转换和隔离 Gateway 集成。
- `docs/automatic-recall.md`：配置、外部模型说明、能力边界和新增客户端合同。
- `docs/checkpoints/2026-10-31-universal-automatic-recall-implementation.md`：完整证据与四端矩阵。

### Task 1: 共用 Recall Core 与合成行为测试

**Files:** 创建 `src/cognivault/recall/{__init__,planning,core}.py`、`tests/test_automatic_recall.py`。

**Interfaces:** `RecallRequest(session_id, request_id, message, recent_user_messages=())`；`RecallPolicy(allowed_domains=("memory", "history", "study"), deadline_seconds=3.0)`；`RecallCore(retrieve, policy=...)`；`await core.recall(request)` 返回结构化 packet 和交付状态；`retrieve` 是 `async def retrieve(queries: dict[str, str], limit: int) -> dict`，只绑定已正式接入的 Gateway。公开精确字段由实现时测试固定并写入接入说明。

- [ ] 先编写行为测试，缺少核心时运行 `uv run --extra dev pytest tests/test_automatic_recall.py -q`，记录预期失败。

```python
# 人工期望；不得由实现函数生成 expected。
# 今天复习什么 -> memory/history/study，主题应包含学习/复习的短词。
# 计算 12 * 8 -> NOT_NEEDED，Gateway 调用数为 0。
# 翻译我上次选择的那句 -> 不能被普通翻译跳过。
# 我之前选择物理还是化学 -> history/memory 的聚焦短查询，不是整句子串。
```

- [ ] 实现本地规则及最多两次无结果改写；校验宿主提供的计划、域与预算；长问题有界切分，不从工具消息取上下文。
- [ ] 实现异步 deadline、当前任务取消和同请求重入；不在不同轮次复用证据，不将空结果当作交付失败。
- [ ] 实现按域轮转、citation+版本去重、保留全部元数据的完整条目预算；固定可信外框和 JSON `untrusted_data`，不拼接可闭合的裸来源。
- [ ] 测试来源角色伪装、候选、旧版本、冲突、过期目标、单域失败、无结果、超时、取消、并发会话、同轮重入、新轮新版本、预算边界。不得声称字符串转义本身证明模型抵抗注入。
- [ ] 运行聚焦测试至通过，独立自审只读边界；提交 `feat: add host-side automatic recall core`。

### Task 2: 核实宿主合同并实现接入

**Files:** 创建 `src/cognivault/recall/hosts.py`、按实际需要的宿主插件/只读连接模块、`tests/test_recall_hosts.py`；创建 `docs/automatic-recall.md`。

**Interfaces:** 消费 Task 1 公开合同；宿主需同时提供真实用户消息/轮次、正式 `retrieve`、取消信号与作答前交付接口。转换失败返回 `UNAVAILABLE` 的非敏感错误码，不调用其他工具。

- [ ] 核对 Codex CLI/当前 Desktop、WorkBuddy PE/安装代码、Hermes 当前实际安装及 provider 生命周期、DSH 安装版本及 `agent/pre-step` / MCP 服务代码。只读指定程序代码与有限安装路径；不扫描个人记录。
- [ ] 保存接口证据（版本、模块和行号/官方链接）。Codex 官方 `mcp_tool` hook 可调用已有连接，但模板展开不等于本地查询规划；command hook 不自动获得原生 MCP。必须在报告中处理这个差异。
- [ ] 对有已核实 API 的宿主先写失败测试，再实现薄适配器；保留已有插件。Hermes provider 禁止 sync/extraction/mirror 写入，不抢占用户既有 provider。
- [ ] 若需要独立只读 MCP 连接，提供正式持久配置合同：显式标识/域/外部模型内容交付确认、固定 Gateway 操作白名单、不接受 prompt 中的连接参数、不从 Host 缺失原生连接时自动启用。当前缺少正式配置即保持不可用；隔离合成配置与生产配置分开。
- [ ] 根据实际冷启动超三秒的证据，增加最小 Codex 持久宿主 MCP 适配器：启动时连接一次，原生 `mcp_tool` 提问 hook 调用 `recall_for_prompt`，只向既有 Gateway 请求 `retrieve_evidence`；逐轮仍按默认三秒。单列原 command 冷启动失败与新持久连接 SDK 回执，不能合并成默认路径 PASS；不写 hook trust、不使用 bypass 参数。
- [ ] 测试实际事件形状、同轮多 step、新轮次、正式连接缺失、取消、来源不能修改配置、无写工具；运行真实安装模块的协议特征测试，准确标为 SDK/协议测试。
- [ ] 运行 `uv run --extra dev pytest tests/test_automatic_recall.py tests/test_recall_hosts.py tests/test_evidence_bundle.py -q`；提交 `feat: add automatic recall host integration contracts`。

### Task 3: 逐端验收、回归与本地交付

**Files:** 更新 `docs/current-state.md`、已批准 spec 的状态说明、实施检查点；必要的合成验收脚本和独立夹具。

**Interfaces:** 消费已实现的正式配置与适配器；能力报告字段至少含客户端版本、配置标识、触发、查询计划、Gateway 命中、证据交付、答案使用、耗时及错误码。

- [ ] 通过 Gateway 准备人工合成的隔离资料；不写生产、不手动传递命中 ID/DTO，原生会话从正常问题独立发现。
- [ ] 可运行的各端独立执行明确偏好、隐含学习建议、普通问题、时效/候选/冲突/注入、错误、取消、重复、新版本与实际进程重启；不能运行的层次说明具体阻塞，不以 SDK 结果补齐。
- [ ] 运行 `uv run --extra dev pytest -q` 和 `git diff --check`；缺依赖先在本工作树隔离环境安装，保留初始失败记录。
- [ ] 独立代码审查共用核心及宿主边界，修复实质问题并跑对应回归；不因难以完成原生验收放宽 AUTO 定义。
- [ ] 更新中文检查点及逐端矩阵，记录 DONE/PARTIAL/BLOCKED/NOT RUN、真实运行证据和下一必要动作；提交 `docs: record automatic recall implementation and host evidence`。保留本工作树，不 push/release。

## 计划自审

设计每项要求均映射到三个任务：核心功能与预算在 Task 1，宿主正式连接/现有插件保护在 Task 2，逐端真实证据及不同验收层在 Task 3。规则语义召回局限和原生入口不足必须如实报告，不能以完成核心宣称四端完成。

实施中补充：正式 MCP 冷启动约 3.7 秒，主代理早期 command 探针约 4.45 秒返回 `UNAVAILABLE`。因此 Task 2 增加会话启动时保持只读连接的宿主适配服务；这是延迟证据驱动的必要修正，不改变查询次数/注入预算或真实 Host 验收门槛。普通问题必须先经共用规则判定跳过，再决定是否建立资料连接。

### Task 4: 用户要求自行解开 Hermes 入口阻塞后的原生适配

**授权：** 用户对入口问题回复“你自己来”；自行在独立目录准备官方运行环境及原生插件，不以找不到旧安装结束。既有用户配置、记忆和插件不迁移、不覆盖。

**Files:** 创建 `hosts/hermes-native-recall.py`、`hosts/hermes-plugin.yaml`、`tests/test_hermes_native_recall.py`；更新 `docs/automatic-recall.md` 与实施检查点。保留原未接线 callback scaffold 兼容合同。

**Interfaces:** 已核实官方 `v0.22.5` / commit `bf0203a794a07aac3ff764972ab9087baa4e4f4e` 的 `PluginContext.get_config(key, default)`、`PluginContext.call_mcp(server, tool, arguments=None, timeout=30)`、`register_hook("pre_llm_call", callback)`；回调真实传入 `session_id`、`turn_id`、`task_id`、`user_message`。原生 MCP 调用必须保留 Host 的 `plugins.entries.<id>.mcp_allowlist` 默认拒绝和 trust gate。

- [ ] 用官方 PM 在独立 `HERMES_HOME` / `HERMES_RUNTIME_DIR` 准备锁定的 Python 3.14 和 MCP extra；不把 CogniVault 的 MCP 1.x 依赖装进 Hermes 的 MCP 2.3 环境，不修改 upstream pin 或降级。
- [ ] 先写失败行为测试，再实现可独立装载的 `register(ctx)`。可信设置为 `python_executable`、`core_source_root`、`gateway_server`、`allowed_domains`、`context_delivery_acknowledged`；前两项必须绝对路径，其余受限校验。固定启动该 Python 的 `-m cognivault.recall.bridge`，仅向本机共用 Core 送真实用户事件，不复制查询规则、不自行连接 MCP。
- [ ] 读取桥的固定 `retrieve_evidence` 请求，通过 `ctx.call_mcp(gateway_server, "retrieve_evidence", arguments, timeout=...)` 执行。拒绝未知操作、超量请求、越权域、错误或截断 envelope。Core 与整个回调仍默认三秒，最多三次联合请求。
- [ ] 使用 daemon worker 承接同步 Host MCP；只取消自己 worker 的原生线程中断位（已核实 `tools.interrupt.set_interrupt(active, thread_id)`），finally 清理该位；不设置原 Agent/其他会话线程的中断，不关闭共享 MCP。检查原回调的 `is_interrupted()`，实际 Host 未能传达取消的部分保留真实限制，不伪造取消通过。保持 contextvars 的 profile scope，不扩权/绕过 allowlist。
- [ ] 测试普通问题零资料调用、真实身份缺失、正常命中、三次无命中、截断/拒绝、重复和新轮、并发取消隔离、原生 API 参数及预算；实际执行 Python bridge 子进程。软件测试不当作真实 Hermes 会话 PASS。
- [ ] 在新隔离 home 通过官方插件装载和正式只读 MCP 验证生命周期，再尝试原生 CLI 正常提问。真实模型认证、登录或信任阻塞保留回执；不读取私人凭据或临时直连替代。独立审查本任务后再更新四端矩阵；没有真实答案使用则不标 `AUTO`。

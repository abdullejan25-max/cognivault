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

- [x] 先编写行为测试，缺少核心时运行 `uv run --extra dev pytest tests/test_automatic_recall.py -q`，记录预期失败。

```python
# 人工期望；不得由实现函数生成 expected。
# 今天复习什么 -> memory/history/study，主题应包含学习/复习的短词。
# 计算 12 * 8 -> NOT_NEEDED，Gateway 调用数为 0。
# 翻译我上次选择的那句 -> 不能被普通翻译跳过。
# 我之前选择物理还是化学 -> history/memory 的聚焦短查询，不是整句子串。
```

- [x] 实现本地规则及最多两次无结果改写；校验宿主提供的计划、域与预算；长问题有界切分，不从工具消息取上下文。
- [x] 实现异步 deadline、当前任务取消和同请求重入；不在不同轮次复用证据，不将空结果当作交付失败。
- [x] 实现按域轮转、citation+版本去重、保留全部元数据的完整条目预算；固定可信外框和 JSON `untrusted_data`，不拼接可闭合的裸来源。
- [x] 测试来源角色伪装、候选、旧版本、冲突、过期目标、单域失败、无结果、超时、取消、并发会话、同轮重入、新轮新版本、预算边界。不得声称字符串转义本身证明模型抵抗注入。
- [x] 运行聚焦测试至通过，独立自审只读边界；提交 `feat: add host-side automatic recall core`。

### Task 2: 核实宿主合同并实现接入

**Files:** 创建 `src/cognivault/recall/hosts.py`、按实际需要的宿主插件/只读连接模块、`tests/test_recall_hosts.py`；创建 `docs/automatic-recall.md`。

**Interfaces:** 消费 Task 1 公开合同；宿主需同时提供真实用户消息/轮次、正式 `retrieve`、取消信号与作答前交付接口。转换失败返回 `UNAVAILABLE` 的非敏感错误码，不调用其他工具。

- [x] 核对 Codex CLI/当前 Desktop、WorkBuddy PE/安装代码、Hermes 当前实际安装及 provider 生命周期、DSH 安装版本及 `agent/pre-step` / MCP 服务代码。只读指定程序代码与有限安装路径；不扫描个人记录。
- [x] 保存接口证据（版本、模块和行号/官方链接）。Codex 官方 `mcp_tool` hook 可调用已有连接，但模板展开不等于本地查询规划；command hook 不自动获得原生 MCP。必须在报告中处理这个差异。
- [x] 对有已核实 API 的宿主先写失败测试，再实现薄适配器；保留已有插件。Hermes provider 禁止 sync/extraction/mirror 写入，不抢占用户既有 provider。
- [x] 若需要独立只读 MCP 连接，提供正式持久配置合同：显式标识/域/外部模型内容交付确认、固定 Gateway 操作白名单、不接受 prompt 中的连接参数、不从 Host 缺失原生连接时自动启用。当前缺少正式配置即保持不可用；隔离合成配置与生产配置分开。
- [x] 根据实际冷启动超三秒的证据，增加最小 Codex 持久宿主 MCP 适配器：启动时连接一次，原生 `mcp_tool` 提问 hook 调用 `recall_for_prompt`，只向既有 Gateway 请求 `retrieve_evidence`；逐轮仍按默认三秒。单列原 command 冷启动失败与新持久连接 SDK 回执，不能合并成默认路径 PASS；不写 hook trust、不使用 bypass 参数。
- [x] 测试实际事件形状、同轮多 step、新轮次、正式连接缺失、取消、来源不能修改配置、无写工具；运行真实安装模块的协议特征测试，准确标为 SDK/协议测试。
- [x] 运行 `uv run --extra dev pytest tests/test_automatic_recall.py tests/test_recall_hosts.py tests/test_evidence_bundle.py -q`；提交 `feat: add automatic recall host integration contracts`。

### Task 3: 逐端验收、回归与本地交付

**Files:** 更新 `docs/current-state.md`、已批准 spec 的状态说明、实施检查点；必要的合成验收脚本和独立夹具。

**Interfaces:** 消费已实现的正式配置与适配器；能力报告字段至少含客户端版本、配置标识、触发、查询计划、Gateway 命中、证据交付、答案使用、耗时及错误码。

- [x] 通过 Gateway 准备人工合成的隔离资料；不写生产、不手动传递命中 ID/DTO。
- [ ] 原生新独立会话从正常问题自动发现，证明真实作答前触发。
- [ ] 可运行的各端独立执行明确偏好、隐含学习建议、普通问题、时效/候选/冲突/注入、错误、取消、重复、新版本与实际进程重启；不能运行的层次说明具体阻塞，不以 SDK 结果补齐。
- [x] 运行 `uv run --extra dev pytest -q` 和 `git diff --check`；缺依赖先在本工作树隔离环境安装，保留初始失败记录。
- [x] 独立代码审查共用核心及宿主边界，修复实质问题并跑对应回归；不因难以完成原生验收放宽 AUTO 定义。
- [x] 更新中文检查点及逐端矩阵，记录 DONE/PARTIAL/BLOCKED/NOT RUN、真实运行证据和下一必要动作；本地提交交付文档。保留本工作树，不 push/release。

## 计划自审

设计每项要求均映射到三个任务：核心功能与预算在 Task 1，宿主正式连接/现有插件保护在 Task 2，逐端真实证据及不同验收层在 Task 3。规则语义召回局限和原生入口不足必须如实报告，不能以完成核心宣称四端完成。

实施中补充：正式 MCP 冷启动约 3.7 秒，主代理早期 command 探针约 4.45 秒返回 `UNAVAILABLE`。因此 Task 2 增加会话启动时保持只读连接的宿主适配服务；这是延迟证据驱动的必要修正，不改变查询次数/注入预算或真实 Host 验收门槛。普通问题必须先经共用规则判定跳过，再决定是否建立资料连接。

### Task 4: 用户要求自行解开 Hermes 入口阻塞后的原生适配

**授权：** 用户对入口问题回复“你自己来”；自行在独立目录准备官方运行环境及原生插件，不以找不到旧安装结束。既有用户配置、记忆和插件不迁移、不覆盖。

**Files:** 创建 `hosts/hermes-native-recall.py`、`hosts/hermes-plugin.yaml`、`tests/test_hermes_native_recall.py`；更新 `docs/automatic-recall.md` 与实施检查点。保留原未接线 callback scaffold 兼容合同。

**Interfaces:** 已核实官方 `v0.22.5` / commit `bf0203a794a07aac3ff764972ab9087baa4e4f4e` 的 `PluginContext.get_config(key, default)`、`PluginContext.call_mcp(server, tool, arguments=None, timeout=30)`、`register_hook("pre_llm_call", callback)`；回调真实传入 `session_id`、`turn_id`、`task_id`、`user_message`。原生 MCP 调用必须保留 Host 的 `plugins.entries.<id>.mcp_allowlist` 默认拒绝和 trust gate。

- [x] 用官方 PM 在独立 `HERMES_HOME` / `HERMES_RUNTIME_DIR` 准备锁定的 Python 3.14 和 MCP extra；不把 CogniVault 的 MCP 1.x 依赖装进 Hermes 的 MCP 2.3 环境，不修改 upstream pin 或降级。
- [x] 先写失败行为测试，再实现可独立装载的 `register(ctx)`。可信设置为 `python_executable`、`core_source_root`、`gateway_server`、`allowed_domains`、`context_delivery_acknowledged`；前两项必须绝对路径，其余受限校验。固定启动该 Python 的 `-m cognivault.recall.bridge`，仅向本机共用 Core 送真实用户事件，不复制查询规则、不自行连接 MCP。
- [x] 读取桥的固定 `retrieve_evidence` 请求，通过 `ctx.call_mcp(gateway_server, "retrieve_evidence", arguments, timeout=...)` 执行。拒绝未知操作、超量请求、越权域、错误或截断 envelope。Core 与整个回调仍默认三秒，最多三次联合请求。
- [x] 使用 daemon worker 承接同步 Host MCP；只取消自己 worker 的原生线程中断位（已核实 `tools.interrupt.set_interrupt(active, thread_id)`），finally 清理该位；不设置原 Agent/其他会话线程的中断，不关闭共享 MCP。检查原回调的 `is_interrupted()`，实际 Host 未能传达取消的部分保留真实限制，不伪造取消通过。保持 contextvars 的 profile scope，不扩权/绕过 allowlist。
- [x] 测试普通问题零资料调用、真实身份缺失、正常命中、三次无命中、截断/拒绝、重复和新轮、并发取消隔离、原生 API 参数及预算；实际执行 Python bridge 子进程。软件测试不当作真实 Hermes 会话 PASS。
- [x] 在新隔离 home 通过官方插件装载和正式只读 MCP 验证生命周期，再尝试原生 CLI 正常提问。真实模型认证、登录或信任阻塞保留回执；不读取私人凭据或临时直连替代。独立审查本任务后再更新四端矩阵；没有真实答案使用则不标 `AUTO`。初次实际为 `AUTH_REQUIRED`；后续已完成独立认证与部分真实模型验收，详见 Task 5，不覆盖初始失败。

### Task 5: 真实模型反例驱动的作答合同修正

- [x] 保留真实run6的缺出处/时序问题，以及SDK路径和不支持模型的原失败。
- [x] 先写来源不能覆盖引用/时序/信任要求的RED回归，再补齐Core固定外框；3秒/8000字符/工具权限保持原合同。
- [x] 136 passed、1 skipped；独立spec PASS、code quality APPROVED；本地提交f026eb7。
- [x] 新原生会话复测引用/时序，完成普通算式/翻译/无结果/学习/来源攻击观察及真实Gateway/CLI重启。
- [ ] 完成剩余原生矩阵、整轮额外调用成本与Study覆盖评估；不能用上述普通用例宣称AUTO。

### Task 6/7/8: 其他宿主的真实原生核验

- [x] WorkBuddy：真实 UserPromptSubmit、原生请求身份、算式跳过。个人召回在连接前失败，隔离认证未完成；实测安装随附交互 CLI 缺 TUI，不能把 `/login` 当成已验证可用入口。GUI 的 userData/config 路径初始化已从安装源码核实，GUI 到独立 CLI 的凭据交接仍未证明。整体 PARTIAL/BLOCKED。
- [x] DSH：官方 profile 插件装载、首次模型 stream 前正式取证、正常问题引源、零召回、无证据、实际 CLI 进程重启后会话恢复并重查；PTC 原生负例 UNSUPPORTED。保留学习传输失败及第二次引用 ID 错误，整体 PARTIAL。
- [x] Codex：已核实只读合成包、真实 CLI 与信任门槛；保留目录/精确定义未授信及TERM启动失败。用户完成精确 hook 信任后，主代理经官方CLI启用其关闭的开关，Installed 1 / Active 1。真实TUI正常问题与实际退出重启自动交付完整两条证据/正确引源，另完成零召回、无结果、来源攻击及学习建议观察，Study覆盖不足仍保留，CLI原生整体PARTIAL；exec探针失败与Desktop分开，未代写trust或bypass。
- [ ] 各端剩余原生矩阵及当前精确 hook/独立账号门槛关闭后，重新评估 AUTO；不改全局/生产配置、不复制凭据、不把 SDK 或组件回执替代 Host 结果。


## 2026-11-01 正式发布收尾授权

用户明确选择“github正式版，没达到要求就继续完成”。此前“不推送/发布”的边界由本次授权更新为：原批准验收和发布门槛全部满足后，允许正常push、PR合并、正式tag及GitHub Release；未达标不发布，不改作预览版。现稳定版v0.9.1，拟新增功能版本v0.10.0；未通过门槛前不提前改版本/宣称正式可用。当前GitHub main核实为98d71a341b15a2197d0b10b1eb4e36b626cdf54f，本地主目录main存在其他工作，不切换/覆盖它。

### Task 9: 关闭真实宿主入口阻塞并定位质量缺口

**Files:** 只读现有Host实现、官方合同和本计划隔离报告；必要的新配置仅在Git外合成验收profile，现有生产配置不动。

- [ ] 核实WorkBuddy桌面与完整CodeBuddy CLI的区别、隔离认证、真实提问事件及持久正式连接；新CLI不能冒充桌面PASS，保持3秒，不以默认HOME搬运凭据。
- [ ] 使用已有Codex精确隔离项目完成当前Desktop原生验收；不以CLI同步到侧栏的聊天冒充Desktop生成。不工具发送其他聊天消息、不绕过trust或工作树owner。
- [x] Study缺口已复现并区分无主题泛化的字面检索局限、显式中英文主题提取错误和后端故障。`a19708a` 修复显式英文主题，79项Core/联合证据回归与独立复审通过；泛问有Memory/History相关证据不等于Study全命中，仍保留原生覆盖缺口。未硬编码默认物理或改写仅整体无结果的预算规则。
- [ ] 从明确客户端合同得出必要修复，先行为RED后最小修复，独立review；若新合同与批准设计冲突，具体说明冲突，不静默修改。

### Task 10: 可安装制品的宿主适配器交付

**Files:** `pyproject.toml`、`scripts/verify_package_contents.py`、`tests/test_release_version.py`、`docs/automatic-recall.md`，仅在证明漏包后修改。

- [x] 实际构建wheel/sdist核实Core、bridge/server与原生适配器资源；原wheel漏包已复现并由`fc22fe0`修复。
- [x] 六项缺适配器归档负例先RED，再配置单一源码force-include和归档审核；17项聚焦测试与独立复审通过，不复制两套维护源码。
- [x] 干净环境从实际wheel导入Core/bridge/server并读取三项原生资源，源码/两种归档/安装资源字节一致；未自动覆盖已有插件或启用生产。最终发行仍需从最终候选重建。

### Task 11: 原验收矩阵与回归闭环

**Files:** 原实施检查点/当前状态/逐端报告，原始合成回执留Git外。

- [ ] 按原spec完成各端缺失的偏好/延续、时效/候选冲突、缺失/权限/超时、取消并发/重复/新轮、其他客户端更新/失效、新版本与实际重启；保留每个实际失败，不拼接成PASS。
- [ ] 验证默认每backend3、联合最多3、总deadline3秒、6证据/8000字符及宿主完整交付；内部backend量必须实测，不伪造计数。模型主Agent追加调用与自动Core分栏。
- [ ] 在最终候选运行完整synthetic suite、stdio、归档隐私/内容、wheel干净安装；完成最终独立代码与验收证据审查。

### Task 12: GitHub正式发布

**Files:** `pyproject.toml`、`uv.lock`、`tests/test_release_version.py`、`docs/releases/v0.10.0.md`、`README.md`、当前状态/检查点。

- [ ] 仅所有前置门槛满足后提升0.10.0，保留依赖pin及原数据结构；安装默认不接个人资料、不自动开hook、不变更生产。
- [ ] 检查所有将公开的Git对象/归档无私有配置、资料、认证或原始Host traces；更新自然中文发行说明，不以工程测试宣传全语义召回。
- [ ] 正常推送候选并建PR，等待精确SHA的Ubuntu/Windows CI；安全合并远端main，不触及主目录其他未发布工作。
- [ ] 合并SHA的CI完成后，从该精确源码重建wheel/sdist/SHA256SUMS，核对annotated tag目标和GitHub正式Release资产digest；不发PyPI、不替换用户安装，除非另有请求。

### Task 13: 后续真实模型反例的作答合同澄清

这是Task5同一安全/时效合同的必要修正，先于Task12发布，不扩展数据能力。新原生回执已证明packet与精确引用交付成立，但DSH存在“先断言当前偏好后降格”、将人工来源中的`role=user`归属当前真人、将明确修订误称未解决冲突的答案反例。

- [x] 保留全部原始反例和独立复审，不改模型答案；先写受保护外框/来源伪装的RED回归，再最小澄清当前断言、来源作者和版本演变的可信作答合同。`900b7fc`已独立审查。
- [x] 不改来源元数据或不确定性，不重写主Agent输出、不加另一个模型、重试回路、写入或来源缓存；预算继续3秒/三次联合/6条/8000码点，新增外框只能通过整条省略适应预算。
- [ ] 聚焦回归、独立代码复审通过后，用新label独立原生重验失败用例与Unicode交付，明确该次成功/失败；旧run不覆盖、不拼接成PASS。

Task11取消补充：官方DSH ACP已核实支持多session与定向`session/cancel`，独立持久合成profile可用于真正共享MCP的取消/新轮核验。只用正式carrier/插件/已声明只读Gateway；先证明装载、身份、交付，不用SDK直接回调或进程kill代替单会话取消，也不把取消时点在模型推理阶段说成检索中途。

### Task 14: 明确有界检索与完整读取的范围

900b7fc的新原生反例中，主Agent将有界返回说成“only”，并在后续工具明确截断时宣称已完整读取。`COMPLETE`表示本次受限检索正常完成，不能证明全库覆盖；原始资料/工具结果仍须保留自己的截断状态。这是既有有界预算和不确定性合同的澄清，不扩大查询权限。

- [x] 先以来源不能覆写“非穷尽检索”可信标记/指导、COMPLETE不等于全库唯一的RED回归固定合同，再作最小外框修正。`0aefced`已独立审查。
- [x] 保持原数据、状态、元数据和预算，不扫描全库、不增加补查或LLM、不改写答案，不用新增免责声明掩盖本次实际缺口。
- [ ] 聚焦回归及独立审查后，以固定官方模型路由的新原生批次复验范围/来源/当前性/引用和Unicode边界。模型不服从的结果仍保留，不能由软件测试替代质量证据。

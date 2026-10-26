# v0.9.0 原生 Host 续跑（2026-10-26）

本轮从干净的 `a12a31688673a6530ee123c092a0ace7ae0b3b65`、`codex/agent-orchestration` 和现有草稿 PR #22 继续。没有读取旧账号聊天导出，没有回退或覆盖更新的工作。总体仍为 **PARTIAL，未发布**。

## 已核验的变化

- GitHub 当前 PR #22 为 OPEN / DRAFT，head 为上述提交，无评论或审查。该提交 push 与 PR 的六个 Windows/Ubuntu job 全部 SUCCESS；Windows 1459 passed / 11 skipped，Ubuntu 1443 passed / 27 skipped，stdio 另 4 passed。这些结果不自动覆盖本轮新改动。
- 当前 Desktop 已能直接读取 `cognivault` 的错题 workflow，以及 `cognivault_orchestration_acceptance` 的 personal-answer 和错题 workflow。历史 `unknown MCP server` 已不是当前连接结论。
- 两个服务原生 `health_report` 均 `ok=true`，且报告相同 startup source 摘要 `428e7f846ac1f4747b39073beb2b453b14c024b956fbccc63ede88ae4a98eaed`。生产配置报告 gateway_version 0.8.0、History ready、Study readable；这不是生产业务完整验收，也不代表包版本由配置字段确定。未检索生产学习、History 或 Memory 内容。
- 隔离服务报告 gateway_version 0.9.0、Study not configured、History unavailable。它的 Memory 经原生 `initialize_memory` 返回 first_deployment / facts=0 / versions=0；只初始化隔离配置，不触碰生产。

## 原生验收发现的问题

### History 收件目录配置

当前隔离 alias 的 migration inbox 原在 Git 工作树内。对纯自造的一条 JSONL 及固定 SHA manifest 调用原生 `ingest_history_sources`，返回 `OUTSIDE_ALLOWLIST`；源码 `private_inbox` 明确拒绝 inbox 任一父目录存在 `.git`。这是有效安全拒绝，不能移除该规则或直接写 History 数据库。

已备份 ignored 隔离配置，仅将该 inbox 改到仓库外的本机隔离目录，并复制本轮自造输入；生产配置、权限与其他 store 路径未变。当前进程重复调用仍返回同样拒绝，证明本次修改尚未应用到现有 Host 进程。需要用户在 Desktop 重载当前项目 MCP 后再验收；本轮没有以新 CLI/test Host 代替。

### MCP 与 SDK 返回形状差异

当前原生工具已成功摄入一页自造 text/plain 文档并注册错题来源。随后通过本地请求桥，把编排 `MCPGatewayPort` 的请求逐个交由**本聊天实际原生工具**执行，桥本身不启动 MCP 客户端、Gateway 或额外 Host。

首次 Study 运行在模型调用前 FAILED / VALIDATION_FAILED。真实原生回执为 `{ok:true,bundle:{source,...}}`，而 handler 需要 SDK Gateway 直接返回的 `{source,...}`。旧 `MCPGatewayPort` 只去掉 `ok`，遗漏了 transport 的固定 `bundle` 包装。该运行没有模型请求、没有分析写入；已注册的来源保留供修复后复用，不重复注册。

修复按操作名显式解开 `get_wrong_answer_bundle` 的 `bundle`，其他 SDK 原有的 item/page/analysis/candidate 包装保持不变；检查允许操作的必需返回字段类型，拒绝错误与成功混杂的回执。回归先复现失败，再修复通过，不从任意单字段盲目解包。

### Study 真实模型与原生 Host 闭环

修复后新任务 `native-study-20261026-r2` 复用已注册来源，只请求模型一次。全部六次业务调用由本聊天的 `cognivault_orchestration_acceptance` 原生工具完成：prepare 读 bundle/page，commit 再核对 bundle/page 后保存分析，verify 独立读取 bundle。桥本身只传递请求和原生回执，不伪造工具响应。

结果 **COMPLETED / verified=true**，`review=null`：

- 来源：`wrong-answer://sha256/5254952c9bc23eb07d274588eeaed7620a1bffa9e4eb88b0547a32d1bbdfdba8`
- 分析：`wrong-analysis://sha256/cc017f211f3f2b45e68e59270d2edf2893fe14480093b41987180565d7a5c00d`，version=1。
- 执行器：`deepseek-harness-no-tools/0.2.1-rc.3.1`；请求及供应商返回型号均 `deepseek-flash`。
- 请求摘要：`91aeb6fb9918e92ac06c877e6ae8fe168de438175a32d26bfd244f6a8be47646`
- 响应摘要：`3d8a8fae003fd1f8bbb9ade281999082f9699d734a804942cd6d0c4daf2e6d70`
- 模型 Result 摘要：`c1da2264921742ed21548d49026323baf5b15f5e2af5c089c85330cd7e2353bf`
- Gateway 回执摘要：`8004b2b7506f2a50fda5864cd9a9100646f06f64731173e21fb4cb270c64dda2`

这是当前 Desktop 原生隔离 alias 的真实业务闭环，不是生产数据验收，也不是 History/Memory 已通过。文档原文是自造 text/plain，不涉及图像识别或 OCR。

## Memory 有界诊断

新增一次真实模型请求使用原 fixture、任务指导、预算与门槛，明确为临时合成 SDK Gateway，**不是 Desktop Host 证据**。校验前固定分类诊断确认 JSON 合法、四项绑定正确、必需字段缺失/额外均为零。结果 `WAITING_FOR_REVIEW / EVIDENCE_REVIEW_REQUIRED`，confidence=0.9、evidence_count=1、uncertainty_count=1，仅触发 `unresolved_uncertainty`。未调用候选写入及回读，不存在可引用的 Gateway candidate。

- 请求摘要：`7a2855f8d1ba16fa457ef06188b69e806a216bf76cd1b5836bb7362b4006268c`
- 响应摘要：`5b25c6e5d077b33c46a9391f082e0f664e0fce6cc5a77cc11ed0930807a05ccc`
- 模型 Result 摘要：`3d4e3b017881b419932698fa9f7322b7c6992f5196f60150df373eca478fea0d`（不是 Gateway candidate ID）

本次没有复现 INVALID_RESPONSE，旧失败不能追溯；不确定性正文未保留，语义原因仍 UNKNOWN，不能猜测成 token 限制或 currentness。诊断 helper 的九组离线负例通过，原字节原样交回产品校验，没有修改模型结果、runtime、阈值或预算。真实请求已停止。

另修复了实际上下文遗漏：`MemoryProposalHandler.prepare` 显式传递 `original.occurred_at` 与 `time_status=known/unknown`。legacy History 只取原 `created_at`，canonical 只取消息元数据 `occurred_at`，缺失保留 `None`。不拿导入、规范化或存储时间替代，不从正文时间反推；不改分类指导或准入门槛，也不声称该遗漏就是前述失败原因。四种来源/时间组合先红后绿。

## 本轮验证

`uv run --locked --extra dev pytest -q -rs tests/test_orchestration_domains.py tests/test_orchestration_history_canonical.py tests/test_orchestration_acceptance.py`：最终冻结代码 **60 passed，退出0**；主线程独立重跑为68.74秒。覆盖所有13个允许操作的MCP返回契约、畸形包拒绝、隔离真实Gateway经MCP transport的prepare与SDK整值相等，以及原发生时间known/unknown。此自动化MCP测试不冒充当前Desktop回执；上方Study闭环另由原生调用取得。

成功任务的同任务重放已返回相同verified receipt，无新的原生请求或模型调用。完整测试、独立审查、包审计与精确提交CI仍须核对本轮最终交付结果。

独立审查发现新增校验曾有两项 P2：真实 `search_memory` 无 `has_more`，真实 History 导入 `errors` 为整数计数，而初版测试的假返回也复制了错误形状。已使用真实隔离 Gateway 经 MCP 的搜索及 reused 导入回执先复现 `BACKEND_BAD_OUTPUT`，再修正对应必需字段；不删除边界断言或把失败改成跳过。这些修正不涉及 Host 重载或生产访问。当前原生 `list_memory_candidates(limit=1)` 独立返回 total=0；未将模型待审结果绕过门槛落库。

最终独立审查再次运行真实传输探针并关闭上述两P2，相关组合 **128 passed / 71.17秒**，没有新增P1/P2。`uv lock --check`、最终双归档构建与隐私审计均退出0；wheel 89项、sdist 344项。最终提交的全量Windows/Ubuntu CI以PR #22 Checks和交付回复为准，不用基线a12a316绿灯替代。

另将最终产品wheel重新安装到本轮既有临时干净环境，`-I`运行canonical与legacy stdio的实际initialize、health、workflow及打包JS runtime检查，返回PASS/0.9.0/退出0；证据范围为clean-installed-synthetic-sdk，不代替当前Desktop。

## 发布边界

当前连接成功不等于三域完整通过。MCP 返回契约与来源时态改动须完成独立复核；History/Memory 仍需在重载后继续原生业务验收，并在最终提交上等待完整 CI。保持草稿，不 merge、tag、Release 或 publish。

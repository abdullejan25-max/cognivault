# Production ingress

当前安装、标准 MCP registration 与 stdio module 使用 `cognivault` / `cognivault.transports.mcp_stdio`。先按 [README](../README.md#upgrade-from-the-former-distribution) 使用新环境，或先卸载 `chatgpt-study-system-v2` 再安装 `cognivault`；旧新 distribution 共享旧 stdio shim 与 `study-migrate` launcher，反向卸载会删除共享文件。Codex helper 只迁移精确识别的旧生成配置；自定义 multi-profile 配置及 WorkBuddy/Hermes 配置需手动变更 registration/module，保留私有 backend 与 capabilities。历史 readonly / P12 isolated aliases 不因改名而自动变化。

下方生产 smoke 与 Host 结果是更名前 P13 的历史验收，保留原范围；本页更新没有读取、迁移或写入生产数据。改名后的真实 Host 与存量数据兼容需另经已授权 Gateway 验收，历史 synthetic 授权不代表可以再次执行 production smoke。

数据恢复与退役检查通过后，正式生产入口复用现有 Gateway 的 Asset、Document 和
Wrong Answer 工具。保留 P13 readonly profile；日常入口使用独立的持久 writable
profile，指向同一组已经核实的生产后端。WorkBuddy canonical History 当前为
**DEFERRED**，不阻塞该入口恢复。

## Persistent profiles

实际配置、后端路径、备份与回执保存在 Git 外。以下仅为占位符：

| Host alias | Gateway profile | Capabilities |
| --- | --- | --- |
| 当前标准 registration `cognivault` | `C:/PATH/TO/PRIVATE/production-ingress.toml` | `read`, `ingest`, `write` |
| 既有 P13 readonly alias，例如 `study_system_p13_readonly` | `C:/PATH/TO/PRIVATE/p13-readonly.toml` | `read` |

两个 profile 的 Study/QMD、History、Assets/Document 后端路径应对应同一生产 target。
日常 ingress profile 使用：

```toml
[permissions]
capabilities = ["read", "ingest", "write"]
```

它无需 `admin` 或 `projection`。P13 专用的 `history.migration_inbox`、`[recovery]`
可仅保留在专用 profile 中；资产文件 inbox 仍按日常原始图片入口的需要配置。
P12 isolated aliases 继续指向其原 isolated target，不能改指生产。

Host 的持久 stdio launcher 使用绝对 executable、`cwd`、`--config` 与源码路径。
Codex 可复用 [.codex/config.example.toml](../.codex/config.example.toml) 的 `uv --no-sync
--project` 和 `PYTHONPATH` 契约，为两个 alias 分别指定上述 profile。
[setup_mcp.py](../.codex/setup_mcp.py) 当前只生成固定的 `cognivault → config.local.toml`，
不是多 profile 合并器；保留并审查既有自定义 Host 配置。默认公开配置仍只读。

在 fresh native Host 中确认持久配置实际加载，并调用 `health_report`。配置存在、
SDK discovery 或 CLI exit code 0 均不代表 Desktop Agent 已连接。WorkBuddy 的
managed entry、名称优先级或 trust/reload 状态需按实际 Host 验证；本页不声称
WorkBuddy Desktop production 写入已 PASS。

## Historical marked production smoke

P13 当时 owner 已明确授权一次生产 synthetic Wrong Answer smoke，包括 append/update。
所有合成题目、原始图片与分析使用唯一的 `P13_PRODUCTION_INGRESS_SMOKE_<RUN>` 和
明确的 `SYNTHETIC ONLY` 标签，避免 slash 分隔标签。`P12_STEP4_` 标记仍属于其
isolated server。

1. 通过 native MCP 直接读取 `study-workflow://wrong-answer`，确认所需工具及能力。
2. 用标记搜索 Wrong Answers。恢复中断的同一次 smoke 时复用其原始 fixture、
   marker、请求参数、身份字段与 idempotency keys。
3. 检查合成原始 PNG，通过 `register_asset` 提交原字节；`fetch_asset` 回读并核对。
   小图可使用 `content_base64`；文件模式仅接受已配置 inbox 中的相对图片路径。
4. `register_wrong_answer_source` 使用实际返回的 `asset_uri`、带标记的题目与合成答案；
   Asset 来源省略 `page_number`。原始 source 不可变，相同注册应复用同一 source ID。
5. `save_wrong_answer_analysis` 提交 `error_type`、`knowledge_points`、`reasoning`、
   `correct_solution`、`review_advice`，`source_refs=[asset_uri]`、`study_relations=[]`、
   固定 v1 key、`expected_version=0`。完全相同请求重放应返回同一 v1 和 provenance。
6. `update_wrong_answer_analysis` 使用当前 `expected_version=1`、新 v2 key 和明确的
   合成修订，得到 v2 及 v1 predecessor link。完全相同 v2 请求重放不得产生 v3。
7. 使用新的 stale probe key 和 `expected_version=1` 再请求更新，应得到 `CONFLICT`。
   不自动改用最新版本重试。
8. fresh Gateway/native Host 通过标记重新发现 source，再读取完整 bundle 与原始
   Asset；版本集合应只有 `[1, 2]`，bundle 按最新优先返回 `[2, 1]`，旧 source/v1、
   时间、provenance、来源引用保持一致。
   不存在标记保持 `total=0/results=[]`。

optional provenance 仅填写已知的 `reported_agent`、`reported_client`、`run_id`；
身份是 caller-reported/unverified。这些字段和 `expected_version` 参与请求摘要，
重放时也必须保持一致。Asset、source 和 analysis 是分开的 Gateway 事务；部分失败
应明确记录已成功部分，按相同请求恢复。

生产 smoke 记录以 marked synthetic 状态保留，可能计入对应的生产总数。当前没有
正式删除接口；本流程不实现删除，也不通过直接 SQL 或文件操作清理生产记录。

## Automated evidence

[test_production_ingress.py](../tests/test_production_ingress.py) 使用同一 invented temporary
target、两个 runtime TOML profile 和官方 in-memory MCP SDK，验证 readonly 工具隐藏及
调用拒绝、原始人工 PNG 的精确字节、source/v1/v2、两次完全重放、stale conflict、
fresh Gateway readback 和字面空结果。PNG 是带 synthetic metadata 的手工有效小图；
该测试验证协议与持久性，不测试视觉推理或实际 Desktop Host 配置。

真实生产 smoke 的结果、Host trace 与回执另行核实并保存于 Git 外。自动化回归不能
替代真实 Host PASS，也不重做既有 P12 验收。

## Historical P13 native production result

Codex 原生生产 MCP 已完成 marked synthetic Asset/source/v1/v2、source 与两版
分析的精确重放、stale CONFLICT、原始 PNG 字节回读、reported/unverified provenance、
版本链与 no-result。Fresh Hermes 原生 Host 从持久 production profile 按 marker
重新发现记录，完整 bundle、原字节与 provenance 等于首轮；Gateway version 0.7.0。
实际 native traces 和 positive usage 在 Git 外核验。最终只有一个 synthetic source
和两个 analysis versions，不存在 v3。

正式 production 与 readonly profiles 已持久配置到 Codex、WorkBuddy 和 Hermes，
指向同一生产后端；P12 isolated 定义保持不变。WorkBuddy GUI 由 owner 操作，本页
不声称本轮 WorkBuddy Desktop write PASS。首次启动新会话需正常重载持久 MCP 配置。
生产 smoke 晚于 recovery checkpoint，额外一份 marked source / 两版合成分析是已知
测试增量，不属于不可恢复的 V1 unique data；原人工 fixture 和正式请求回执留在本地。

Fresh Hermes 还通过同一持久 writable profile 原生执行 v2 完全重放：请求、响应、
provenance 和 bundle 均与 Codex 回执一致，版本链仍为 [2, 1]。

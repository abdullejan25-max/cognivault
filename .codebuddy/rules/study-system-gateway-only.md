# Gateway-only Data Access（CogniVault）

## 适用范围

涉及 V2 的 **Study / History / Sources / Wrong Answers / Assets** 的任何数据访问——读取、计数、检索、存在性判断、写入——一律通过 `cognivault` MCP / Gateway 工具完成。历史 `study_system` 名称与已有 provenance 保留原样；P12 isolated aliases 仍按原配置访问隔离 target，不改指生产。

## 硬禁止

- 直接打开 Gateway 的 private SQLite 或任何 store 文件，**即使以 `mode=ro` 打开、即使只跑 `SELECT count(*)`**；
- 直接读取 private runtime 目录下的文件、快照、日志来取数或佐证；
- 用 SQLite / 文件系统的结果**替代、补充或交叉验证** Gateway 的返回值。

理由：架构边界是 **Agent thinks; Gateway executes**。绕过的是权威边界、schema 稳定性与审计，不是写保护——只读打开同一份 DB 同样是绕过。

## 计数拿不到时怎么办

Gateway 未暴露某个计数 → 明确写 **「该计数当前没有 Gateway 证据」**，然后用现有 Gateway 工具能提供的最接近证据来表述。

**不退回 SQLite / 文件补数。** 确实需要该计数时，另开议题讨论是否新增一个最小只读 Gateway capability。

## 语义约定（不许自己推断）

- 在 P11 / v0.2.0 / v0.3.0 的历史范围中，`canonical messages = 0` 且 source records > 0 是 **预期的 source-only 状态**，**不是**「迁移尚未发生」。该历史数字不代表当前全平台 History 总量；当前状态见 `docs/current-state.md`。
- `documents / pages / chunks` 计数**不能**用来判断 Study 是否已 ingest；Study 由 authoritative StudyVault / QMD 层经 `search_study` 提供。
- 检索返回 0 结果 ≠ 系统故障。如实报 0，不猜原因，不补数据，不 hallucinate。

## 报告约束

对外报告不输出 private 绝对路径（Gateway runtime DB、本地 store 路径等）。只用逻辑标识：`source_id`、`source_record_id`、`document://` URI、相对路径。

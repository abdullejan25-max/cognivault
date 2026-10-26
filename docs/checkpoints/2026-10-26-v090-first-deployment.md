# v0.9.0 History 首次部署与初始化竞态修复

承接 `52c3c6e` 与[此前原生检查点](2026-10-26-v090-native-resume.md)。总体仍 **PARTIAL / 未发布**；不把本轮 SDK 测试当作当前 Desktop 新工具已加载。

## 真实阻塞定位

重载后的隔离 Host 报告新 instance/source 摘要；原 manifest `0ecabad592dd70a39cabf135ab39f249510dddabd54626ec19a5716aa9c537fd` 已通过收件目录 allowlist。原生导入返回 `ok=true`，但唯一条目为 `HISTORY_UNAVAILABLE`、imported=0、errors=1，故业务导入失败。不能因顶层 ok 而误报成功。

当前正式 MCP 原先只有 Memory 首次初始化，没有 History 对应入口。History 存储默认不创建缺失文件，因而不是增加模型调用或更改提示词能解决的问题。本轮没有直接创建、打开或修改隔离/生产 History 数据库。

## 最小正式入口

新增 `Gateway.initialize_history` 与严格无参数 MCP `initialize_history`。仅已配置 SQLite 且有 admin 的 profile 可以发现；定向调用仍独立检查 admin，普通 read/write/ingest 权限不足。只操作配置绑定的路径，不接受调用者路径，不自动在检索或导入中初始化。

`SQLiteHistoryBackend.initialize_empty` 要求主库及 `-wal/-shm/-journal` 全部不存在，包括失效链接；已有条目一律保留、拒绝接纳或迁移。旧 `initialize()` 的兼容语义不变，现有数据库没有迁移。

## 审查发现与修复

初版沿用 Memory 的保留文件后直接由 SQLite 建表的方式。独立故障注入证明：schema 执行前若 journal 被并发替换，SQLite 可能删除外来文件并返回成功。旧 Memory 首次初始化也被同一探针实证存在该 P2。本轮同时修复两者的首次创建流程，既有业务 schema 与候选审批规则不变。

现在在 `:memory:` 完成原 schema（Memory 包含 candidate schema）、commit、serialize；SQLite 从不打开最终路径。四个文件都以 `O_EXCL` 预留并保持句柄，写入前后比较 fd/path 的设备、文件身份、普通文件类型、链接数和预期尺寸。数据库仅通过原持有句柄完整写入和 fsync；辅助文件始终应为空。任何冲突、替换、短写错误、fsync 或序列化不支持均失败并保留现场，不按路径清理或自动重试接纳残留。

Windows 产品仍保持原 `os.open` 更严格的共享行为。测试专用 creator 允许 `FILE_SHARE_DELETE`，以额外实证真正 unlink/替换攻击下的身份保护；没有放宽产品共享设置或安全断言。

## 验证证据与范围

- History 专项 28 passed；独立 History/transport/业务组合 99 passed，无跳过。
- 主线程五文件组合 170 passed / 1 skipped；跳过为既有 Windows 符号链接权限测试，History 失效 junction 替代覆盖实际通过。
- Memory 独立复核 97 passed / 1 skipped。原 journal 替换探针与同 inode 内容修改探针均返回 STORAGE_UNAVAILABLE，外来字节完整保留，两项 P2 关闭，无新增 P1/P2。
- 主线程另补“serialize 方法存在但抛 NotImplementedError”的先红后绿回归，使 History 与 Memory 一样返回稳定 STORAGE_UNAVAILABLE，而不是异常文本；保留全部空预留项。
- 补充后的 History/Memory operations/write safety/candidates 组合 **118 passed / 1 skipped，30.94秒**；stdio 两入口回归 **4 passed / 15.12秒**，均退出0。锁文件检查通过，wheel 90项/sdist 348项的构建和隐私审计通过；重新安装候选wheel后，新初始化API导入及canonical/legacy实际initialize/health/workflow检查均PASS。这些仍是clean-installed SDK范围，不代替新原生工具验收。

上述均使用真实临时 Gateway/SQLite/MCP 传输和自造资料，不触碰实际 alias 的存储。完整最终测试、构建、安装、Windows/Ubuntu CI以本轮最终提交 Checks 为准。

## 后续门槛

新工具需经当前 Desktop 重载后实际发现，再只对原隔离 alias 执行首次初始化、原 manifest 导入、规范化与独立回读；生产配置和权限不变。之后继续同一编排架构的 History/Memory 真实模型任务。Memory 的待审输出不能绕过原置信度/不确定性门槛落库，不自动批准候选，不猜测旧 INVALID_RESPONSE 根因。所有门槛未过前保持 PR 草稿，不合并、打 tag 或发布。

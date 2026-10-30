# 2026-10-31 Memory 并发校验修复检查点

## 范围与根因

用户已授权修复，并在确认为项目通用问题后发布 v0.9.1。修复以 v0.9.0 提交 `d730bcfb4562ae6dee8b7a49a8daf41f2cbb533a` 为基线；首次全量的 `1529 passed / 1 failed / 17 skipped` 与复跑失败保留于[原始报告](2026-10-31-project-test.md)。

已用真实合成 journal 建立确定性复现：元数据读取之后删除 journal，再调用正常的 Memory 写入和回读。旧实现稳定返回 `STORAGE_UNAVAILABLE`，说明多次文件状态读取本身存在竞态，不依赖个人数据库或 MCP 配置。真实并发复跑与确定性时序注入为独立证据。

实现只修改 `SQLiteMemoryStore._validate_path`：父路径仍检查重解析点；每个叶子使用一次不跟随链接的 `lstat` 快照；只接受叶子不存在，拒绝其他错误、非普通文件、硬链接、符号链接和 Windows reparse 属性。schema、事务、WAL 拒绝、幂等键、版本冲突和权限不变。没有宣称消除所有校验后恶意替换路径的竞态。

## 已取得证据

| 验证 | 本次结果 |
| --- | --- |
| 旧实现确定性 RED | 1 failed、17 passed；失败为 journal 读取后消失，异常 `STORAGE_UNAVAILABLE` |
| 当前版本断言 RED | 2 failed；源码仍为 0.9.0，与授权的 0.9.1 不一致 |
| 修复后路径/版本/metadata | 39 passed，7.43 秒；其中 24 项路径回归 |
| Memory 相关测试 | 141 passed、1 skipped，41.10 秒；此轮早于最后 6 项安全回归，后者纳入完整回归 |
| 原并发创建与带 guard 的并发修订 | 10 次独立 pytest 进程，每轮 2 passed，合计 20 passed，无失败 |
| 独立只读审查 | 无阻断发现；独立跑相关测试 20 passed、1 skipped，追加 6 项安全测试 6 passed |
| lock | `uv lock --offline` / `uv lock --check` 通过，只改项目自身 0.9.0 → 0.9.1，未升级依赖 |
| 初次包构建/审计 | wheel 90 entries、sdist 355 entries；最终发行归档须包含完成后的检查点并再次审计 |
| 完整文档候选构建/审计 | wheel 90 entries、sdist 356 entries；重建 wheel 的 SHA-256 与已干净安装测试的 wheel 一致 |
| 干净安装 | Python 3.11 独立环境，版本 0.9.1、Harness JS 资源可用 |
| 干净包 MCP | canonical / deprecated 两入口均通过 initialize、身份、health、两个 workflow 和只读工具发现 |
| 干净包 Memory 并发 | 4 线程创建 8 个同一事实请求复用一个 ID；4 个修订请求仅一个成功，其余 3 个 `CONFLICT`；版本数为 2 |
| 语法与依赖 | Python `compileall`、开发环境 `uv pip check` 通过 |
| 本地完整回归 | 1555 passed、17 skipped、0 failed，679.44 秒（11 分 19 秒）；JUnit 共 1572 项，与控制台一致 |

锁定测试环境为 Python `3.11.15`、pytest `9.1.1`、MCP SDK `1.30.0`、pypdf `6.19.0`。干净 wheel 安装按依赖范围重新解析，MCP SDK 为 `1.30.0`、pypdf 为 `6.21.4`；没有把该环境当作锁定环境的重复验证。

本轮 17 项 skip：7 项缺少 symlink / junction 权限、4 项 POSIX 专属、1 项可选 PyMuPDF、3 项未显式指定的合成 QMD 程序、1 项真实资料 QMD、1 项真实 Codex CLI Host。本轮没有把 v0.9.0 测试时补测通过的可选项并入 v0.9.1 的完整通过数。当前补丁不改 QMD 或 PDF 实现，补丁新增的 24 项路径回归均实际执行通过。

## 跨平台与发行证据

代码提交 `457fe10d973f3e4559d241c36c452dfc4eb9106b` 已触发 [CI 41096772144](https://github.com/abdullejan25-max/cognivault/actions/runs/41096772144)。其中 Ubuntu 完整测试为 **1545 passed、27 skipped**，stdio smoke 为 **4 passed**，Ubuntu 构建与安装通过。这是代码提交检查，不能替代随后文档提交或合并提交的最终检查。

发布必须通过最终提交的 Windows / Ubuntu 测试和包检查。精确合并 SHA、最终 CI、v0.9.1 tag、发行资产及校验和以 [v0.9.1 GitHub Release](https://github.com/abdullejan25-max/cognivault/releases/tag/v0.9.1) 的平台回执为准；创建这些记录之前不能仅凭版本号称已发布。

## 边界

本次全部资料为隔离合成数据，未读取、写入或迁移生产资料，未调用外部真实模型。当前聊天没有注册 `cognivault`，没有修改本地配置或用脚本绕过生产 Gateway；当前 Desktop 原生 Host 和生产端到端仍为 NOT RUN。独立 SDK/stdio 结果不替代这两个验收层。

符号链接权限和 POSIX 专属测试的 skip 原因按每个平台实际输出保留。原始日志、JUnit 和临时诊断脚本不进入公开仓库。此次只发布 GitHub tag / Release，不表示 PyPI 等包索引也已发布。

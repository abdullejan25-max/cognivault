# 2026-10-24 Agent 编排层交付检查点

总体状态：**PARTIAL**。通用 SDK、五种任务映射及隔离合成验收已实现；生产 Desktop 与真实模型接入未验收。不得将本检查点称为正式版本发布或生产可用证明。

## 环境、设计与实现

- 基线：`a8f12d575ea4f3818dd9d83aca79b7c75df0bba5`，与调查时获取的 origin/main 一致，工作树起始干净；分支 `codex/agent-orchestration`。
- `DONE`：Python >=3.11、锁定依赖安装；沿用现有 Gateway、权限、Study 版本幂等、History 来源导入和 Memory 审阅机制。没有业务 schema 变更或新数据库。
- `DONE`：设计与威胁模型见 [设计](../superpowers/specs/2026-10-24-agent-orchestration-design.md)，接口用法及实际限制见 [SDK 文档](../agent-orchestration.md)。
- `BLOCKED`：当前 Host 直接读取已知 `study-workflow://wrong-answer` 返回 `unknown MCP server 'cognivault'`；当前工作树没有 `.codex/config.toml`。未修改信任、权限或生产配置。
- `PARTIAL`：本机 DeepSeek Harness CLI 版本和帮助实测成功；真实模型执行因未验证的工具/profile 隔离阻塞，认证状态未知。详情与安装程序源码 hash 见 [真实接入报告](../agent-orchestration-harness.md)。

新增文件职责：

| 文件 | 原因 |
| --- | --- |
| src/cognivault/orchestration/models.py | Task / Result / Descriptor / Receipt、状态与约束 |
| src/cognivault/orchestration/adapters.py | 注册、能力筛选、证据状态、合成执行器 |
| src/cognivault/orchestration/journal.py | 私有元数据、OS 锁、原子落盘、损坏拒绝 |
| src/cognivault/orchestration/engine.py | 编排、有限重试、取消、验证、恢复与回读 |
| src/cognivault/orchestration/domains.py | 三域固定操作和既有 MCP session / SDK port |
| src/cognivault/orchestration/harness.py | 明确阻断的 Harness 骨架与有界协议解析器 |
| src/cognivault/orchestration/__init__.py | 显式导出；import 不加载私人 runtime |
| tests/test_orchestration*.py | 合成协议、恢复、安全、三域 Gateway / MCP 测试 |
| docs/agent-orchestration*.md、spec、plan、本检查点 | 调查、设计、使用、证据、限制与续跑 |

没有删除业务文件；README/current-state 仅增加未发布能力入口。不提高版本号、不新建标签。

## 测试与三域验收

基线完整套件：**1253 passed、16 skipped，Windows / Python 3.11.15，539.94 秒**。这是实现前基线，不能替代最终分支测试。

最终分支的执行结果、精确提交 CI 与 Git 交付状态将在检查完成后记录于下方“最终验证记录”。

| 验收 | 状态与证据范围 |
| --- | --- |
| Study | DONE（隔离合成 SDK）：版本/幂等保存、来源绑定、独立回读、跨首页旧版本恢复、注入结构与不一致知识点上限拒绝 |
| History | DONE（隔离合成 SDK）：原文指纹绑定、派生摘要隔离、manifest hash 校验、原始字节保留、重复导入、逐来源独立 hash/大小回读 |
| Memory | DONE（隔离合成 SDK）：原文 exact quote、来源完整性、pending 候选、冲突回显、无自动晋升；Study-only 主来源提议未接入 |
| 三域联合检索 | DONE（隔离合成 SDK）：成功、无证据与部分/全部不可用状态保留 |
| MCP protocol | DONE（内存连接的合成 SDK）：正式 create_mcp_server roundtrip，读者权限拒绝、错误结构拒绝；不是 Desktop Host |
| DeepSeek Harness CLI | DONE：安装版本/根帮助；真实文本、图片、模型服务恢复均 NOT_TESTED / BLOCKED |
| 当前 Desktop 生产验收 | BLOCKED：当前会话无 cognivault registration；没有读写真实生产数据 |

## 安全审查

独立审查复现并修复：VERIFYING 恢复转换遗漏、恢复等待误判业务审阅、矛盾 JSON 日志被当成完成、取消未清理即重试、Study 历史版本分页和知识点上限不一致、wrong-analysis 逻辑 ID 遗漏。每项补充失败测试后修复。

默认拒绝外传；模型输出无工具路由权；权限仍由 Gateway 执行；Memory 仅候选；原始聊天不被模型摘要替换。没有读取真实 private store、微信、凭据值，没有上传私人材料；本轮未实际调用外部模型。任务回执的 reported Agent identity 不是身份认证。

`PARTIAL` 限制：进程内适配器代码属于受信插件，无法强杀恶意 Python；journal 需可信私有目录和父目录 ACL；原候选由可信应用安全保留才能恢复；摘要/检索不持久化；History 分页由调用方继续；Memory 不自动处理任意来源或完整语义冲突；图像分析质量未用真实模型验证。没有新增依赖；既有锁定依赖不代表已执行全面漏洞数据库审计。

## 续跑指令

继续 `codex/agent-orchestration`，先核对 HEAD、status 和本检查点的最终验证记录。不要把合成测试当生产 Host 证据。读取环境报告及 Harness 报告，保持 Gateway-only；不要直接打开任何 private store。

1. 当前 Desktop 正式加载本项目受信 `.codex/config.toml` 后，直接读取已知 workflow 并运行最小只读 health；保留 CONFIGURED/CONNECTED/VERIFIED 区分。没有注册时不要启动 CLI 替代。
2. 实现并独立验收 Harness 的隔离 profile（零业务工具、禁止私有 instructions/skills/profile 继承）、明确认证注入、stdin、输出限制、超时/取消进程树回收。仅传自造文本做真实调用；成功后再考虑获准图片。当前骨架无启用开关。
3. 宿主应用接线 SDK，与正式既有 MCP session 绑定；生产操作以最小、可审计授权范围验收。先只读，再对明确获准的隔离业务数据写入和独立回读。
4. 未完成真实接入前保持 PARTIAL；合并与发布须以检查条件、项目流程及实际验证为准，不凭代码存在提高能力状态。

## 最终验证记录

- `DONE`：开发过程中完整套件 **1351 passed、16 skipped，606.69 秒**。该次收集包含首批 98 个新增测试；之后审查新增测试及修复以独立最终回归和精确提交 CI 为准。
- `DONE`：`uv lock --check`、`compileall`、`git diff --check`；wheel + sdist 构建及内容隐私审计；临时干净 Python 3.11 环境安装 wheel 后导入新模块与历史 compatibility shim。
- `DONE`：最终新增回归 **119 passed**，覆盖协议、三域真实合成 Gateway、MCP 回路、进程间 OS 锁、图片完整性和独立审查修复。合成适配器被禁止声明 external/local scope，避免伪装真实调用。
- `NOT_TESTED`：精确最终提交 Windows / Ubuntu CI；等待分支提交后以实际 GitHub 结果更新，不将本机 Windows 测试当 Ubuntu 证据。

本机 16 项 skip 原因：真实 Codex CLI Host smoke 未显式启用（1）；真实 QMD 参数未提供（4）；可选 PyMuPDF 未安装（1）；Windows 不支持/未授予 symlink、junction、FIFO 或 POSIX mode（10）。未启用真实数据测试，未将 skip 算作通过。

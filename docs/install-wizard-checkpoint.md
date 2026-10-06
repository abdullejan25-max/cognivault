# Windows 安装向导验收

日期：2026-10-06。状态：本地开发完成，未发布新的 GitHub Release。正式稳定版本仍为 v0.8.0；本记录不改写其既有发布证据。

## 用户可见行为

下载完整源码、解压后双击 `install.cmd`，自动准备 uv、Python 3.11 和锁定依赖，生成默认只读 profile 与 Codex 项目配置。首次安装不要求预先安装 Python、uv、Git，也不要求编辑 TOML。

默认 profile 的 Study、History、Assets 均明确为 `not_configured`；不建立假资料路径，不自动打开、创建或导入业务数据。安装器列出真实客户端接入的最后一步。已有本地配置的原始字节、路径与 capabilities 保留。

详细操作和范围见 [安装说明](installation.md)。首版 Windows + Codex 自动配置，其他本机客户端得到启动参数；QMD、OCR 及 ChatGPT hosted 接入维持现有可选配置方式。

## 验证证据

| 验证 | 结果与边界 |
| --- | --- |
| 最终完整合成 pytest | **875 passed / 12 skipped**。覆盖新默认 profile、runtime 兼容、helper、Windows 入口及最终配置保护回归。 |
| 最终相关回归 | **37 passed**，包含 **8 个 Windows 入口测试**。入口、错误、超时、子进程清理、模块路径与 5.1 兼容均通过。 |
| PowerShell 7 临时安装 | **PASS**。清除 uv/Python PATH、中文与空格目录、官方 uv 引导、wheel 安装和导入、重复运行通过。 |
| PowerShell 5.1 临时安装 | **PASS**。清除 uv/Python PATH，并设置独立 Python 下载/缓存目录与 only-managed preference；官方 uv 引导、Python 环境、wheel 导入和重复运行通过。 |
| 安装后本机 MCP | **SYNTHETIC PASS**。通过安装器生成的正常启动参数初始化，调用 `health_report` 与已知 workflow URI；使用默认无数据 profile。 |
| 客户端环境隔离 | **PASS**。外部 `UV_PROJECT_ENVIRONMENT` 覆盖不会把安装器或生成的客户端参数导向另一个环境。 |
| 现有配置保护 | **PASS**。重复运行、移动 checkout、自定义冲突、替换为其他启动程序和无效 TOML 测试通过。 |
| 依赖锁与构建 | **PASS**。`uv lock --check`、wheel/sdist build、内容隐私审计通过；源码包包含入口、引导、配置程序、helper、模板与锁文件。 |
| PowerShell 文件编码 | **PASS**。源码包中的脚本保留 UTF-8 BOM，5.1 能正确解析中文文本。 |
| 独立代码审阅 | **Ready / Yes**。接入环境、未知配置拒绝、进程树清理、stdout 和跨 edition 模块发现问题均修正后复核。 |

以上安装检查实际运行软件引导，但领域数据均为合成或明确未配置。没有读取或写入 production 数据，没有替代当前 Gateway 数据访问，也没有借此声称普通 Desktop 会话已经通过。

## 修正的关键边界

- uv 使用绝对 executable，生成客户端参数固定项目 `.venv`；不依赖旧客户端刷新 PATH。
- 绝对 uv 模式下，保留未知自定义配置并报告不满足启动要求的冲突，不错误报告安装完成。
- 自有生成配置识别同时检查 uv 程序名与整份内容；保留用户换成其他启动程序的配置。
- native 子进程 stdout/stderr 分别异步读取，显示完成提示与后续步骤。
- PowerShell 5.1 按精确子进程 PID 清理整个进程树，超时后不继续后台安装。
- native 子进程重新发现自己的 PowerShell 模块，避免继承另一个 edition 的 `PSModulePath`。
- 官方 uv 引导使用 unmanaged 安装位置，不更改 PATH、shell profile 或其他 uv 安装的 receipt/update 配置；相关环境变量在 finally 中恢复。

## 尚未建立的证据

- 普通 Codex Desktop 新会话的项目 trust 与真实 Host 加载：**未在本任务复验**，需正常打开项目、确认信任并调用 `health_report`。
- Hermes / WorkBuddy 的本次真实 Host 验收：未执行；安装器不编辑它们的用户级或 managed 配置。
- 新安装接入用户自己的 QMD、History、Assets 与错题写入：未执行；资料仍需明确配置。
- 本功能尚未 push、merge 或建立新 tag/Release，公开 v0.8.0 不包含本次安装器。

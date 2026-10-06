# 安装 CogniVault

## Windows 新用户

1. 从 GitHub 下载源码包，解压到自己准备长期使用的目录。
2. 双击根目录的 `install.cmd`，等待三个阶段完成。首次运行需联网下载软件依赖。
3. 在 Codex 中打开这个目录，确认信任项目，新建会话并发送：`调用 cognivault 的 health_report，检查当前配置状态。`

无需预先安装 Git、Python、uv，也无需编辑配置文件。安装入口优先使用 PowerShell 7；未安装时使用 Windows 内置 PowerShell 5.1 的兼容引导。入口采用当前进程的 execution policy 设置，不改变用户级设置。

安装器复用已有 uv；找不到时通过 [Astral 官方安装渠道](https://docs.astral.sh/uv/getting-started/installation/)安装固定的 uv 0.12.17 到当前用户工具目录，并禁止修改 PATH 与 shell profile。uv 自动准备 Python 3.11，项目依赖安装到当前源码目录的 `.venv`。Codex 的启动配置记录 uv 的绝对路径，因此刚安装完无需依赖客户端刷新 PATH。

配置和启动依赖这个源码目录。以后移动目录，可再次双击入口重新准备虚拟环境和刷新自有生成的 Codex 配置；如果原虚拟环境不能迁移，uv 将按项目需要重建。不要在安装完成后删除源码目录。

## 默认完成到哪一步

安装器生成被 Git 忽略的 `config.local.toml` 和 `.codex/config.toml`，默认仅开放 `read` 能力。默认 profile 为：

```toml
[gateway]
version = "0.8.0"

[study]
backend = "not_configured"

[history]
backend = "not_configured"

[assets]
backend = "not_configured"

[permissions]
capabilities = ["read"]
```

`health_report` 能检查 Gateway 配置；新安装的 Study 搜索、History 查询与错题保存尚未可用，未配置状态不会伪装成空查询成功。真实 Codex 会话能否加载 MCP，还取决于打开正确项目、确认信任及创建新会话。安装器不会自动修改项目信任。

## 按需接入自己的资料

- **学习搜索：** 按根目录 `config.example.toml` 的 Study/QMD 示例替换本地 profile 的 `[study]` 部分，指定自己的资料目录与受支持的 QMD 2.8.3、Node.js 和索引配置。该步骤适用于已经准备好这些组件的用户。安装器不自动扫描或索引个人目录。
- **聊天历史：** 按示例启用自己的 History 后端；导入需经正式 Gateway 操作，不会随安装自动发生。
- **文档与错题：** 配置自己的 Assets 后端；按用途明确添加 `ingest` 或 `write` 权限。原题与 Agent 分析仍分开保存。
- **OCR：** 按 README 的可选组件说明安装 Tesseract、语言数据和 Python extra。

仅在 Git 忽略的本地配置中填写这些位置，不提交到公开仓库。安装器不打开、创建、迁移或导入业务数据库，也不把原始资料复制进项目。

## 已有安装与重复运行

已有 `config.local.toml` 时，安装器仅检查 TOML 是否可解析，保留文件原始字节、数据位置和权限。它不以 TOML 解析成功来宣称对应后端健康。未知的 Codex 自定义配置也保留；冲突时停止并给出检查文件的位置。helper 精确识别的自有生成配置可刷新。

旧名 `chatgpt-study-system-v2` 的用户仍需遵守 README 的升级顺序。安装器仅同步本项目环境，不自动卸载其他环境中的旧包。

## 失败时

窗口会保留失败阶段与提示。网络恢复或文件权限问题解决后，可直接重新双击；已完成的依赖下载由 uv 缓存复用。

| 提示 | 处理 |
| --- | --- |
| 源码包不完整 | 解压完整源码包，确认根目录有 `uv.lock` 和 `src` |
| 准备 uv 或下载依赖失败 | 检查网络能否访问 Astral、GitHub 与配置的软件包源，再重试 |
| 无法处理本地配置 | 检查 `config.local.toml` 的 TOML 格式及写入权限 |
| Codex 配置未生成 | 检查并保留 `.codex/config.toml` 的自定义内容，参考 Codex setup 文档手动合并 |
| 实际会话找不到 cognivault | 确认打开的是安装目录、已信任项目，并启动新会话 |

安装步骤设有超时，失败返回非零退出码。不会为了继续安装而改用缺少依赖的运行方式。

## 其他客户端与自动化

在终端运行 `install.cmd -NonInteractive -HostName none`，安装器会输出可复制的本机 stdio MCP 启动参数。按客户端说明添加参数；不会覆盖 Hermes、WorkBuddy 的用户级、managed 或其他 MCP 配置。

自动化使用 `install.cmd -NonInteractive`，将这个参数放在最前面可避免关闭窗口前的等待。高级 PowerShell 参数 `-TimeoutSeconds` 控制依赖安装超时；`-ToolsDirectory` 可指定 uv 引导的绝对输出目录，用于便携软件部署或隔离验证。已有 PATH 中的 uv 仍优先复用。

macOS/Linux 按 README 的手动步骤安装。ChatGPT Web / Secure MCP Tunnel 仍按现有技术指南处理，安装器不配置账号或开启隧道。

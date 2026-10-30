<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/cognivault-icon.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/cognivault-logo.png">
    <img src="docs/assets/cognivault-logo.png" alt="CogniVault" width="300">
  </picture>
</p>

<h1 align="center">CogniVault</h1>
<p align="center">给 AI 助手用的本地学习资料库。</p>
<p align="center">
  <a href="https://github.com/abdullejan25-max/cognivault/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/abdullejan25-max/cognivault/actions/workflows/ci.yml/badge.svg?branch=main" alt="main 分支检查"></a>
</p>
<p align="center">
  <a href="https://github.com/abdullejan25-max/cognivault/releases/latest">下载</a> ·
  <a href="docs/installation.md">安装说明</a> ·
  <a href="docs/current-state.md">项目进展</a>
</p>

CogniVault 让 AI 助手查阅本机的教材、错题和聊天记录，也能保存你希望长期记住的内容。不同客户端接入同一套数据后，可以共用已有资料。

资料放在你指定的本地目录。AI 助手负责理解问题、整理答案；CogniVault 负责检查权限、查找资料和保存记录。

## 能做什么

- **查教材和文档**：搜索接入的资料，查看 PDF 原页，需要时识别扫描页中的文字。
- **整理错题**：保留题目与附件，单独保存 AI 分析及其修订记录。
- **找聊天记录**：导入后搜索历史对话，回到原文核对上下文。
- **保存长期记忆**：记录你明确要求记住的内容，保留来源和修改记录。

## 先跑起来

### Windows

1. 下载[最新版本](https://github.com/abdullejan25-max/cognivault/releases/latest)中的源码压缩包（`Source code (zip)`），解压到准备长期使用的目录。
2. 双击根目录的 `install.cmd`，等待安装完成。它会准备 uv、Python 3.11 和项目依赖，无需提前安装这些工具。
3. 用 Codex 打开这个目录，确认信任项目并新建会话，发送：

   ```text
   调用 cognivault 的 health_report，检查当前配置状态。
   ```

安装器先准备运行环境和默认只读配置，首次安装不会接入个人资料。要搜索教材、导入历史或保存错题，还需要配置相应的数据目录和权限。教材搜索和扫描文字识别需要另装组件，具体步骤见[安装说明](docs/installation.md)。

WorkBuddy、Hermes 等客户端可以使用同一个本地服务，配置入口见下方。

### 手动安装

需要 Python 3.11 或以上版本，以及 [uv](https://docs.astral.sh/uv/)。

首次安装时，将 `config.example.toml` 复制为 `config.local.toml`，在里面填写自己的数据位置和权限；已有安装沿用原配置。使用旧包 `chatgpt-study-system-v2` 的用户，请先按[旧包升级步骤](#upgrade-from-the-former-distribution)处理。然后在仓库根目录运行：

```shell
uv sync --project . --no-editable
```

更新源码或依赖后，再运行一次这条命令。

## 连接 AI 客户端

CogniVault 通过 MCP 连接客户端。MCP 是 AI 客户端调用外部工具的协议；这里使用本机的 stdio 连接，由客户端启动 Gateway（负责权限检查和数据读写的本地服务）。

| 客户端 | 配置说明 |
| --- | --- |
| Codex Desktop | [接入步骤](docs/codex-host-setup.md) |
| WorkBuddy | [接入步骤](docs/workbuddy-host-setup.md) |
| Hermes | [接入步骤](docs/hermes-host-setup.md) |

其他支持 stdio MCP 的客户端，需要按各自的说明配置并验证连接。启动命令的形式如下，路径请换成自己的：

```text
uv run --no-sync --project <repository-path> python -m cognivault.transports.mcp_stdio --config <absolute-config-path>
```

`--no-sync` 不会安装依赖，启动前应先完成上一节的同步。`.codex/setup_mcp.py` 只负责生成 Codex 的项目配置，其他客户端不需要运行它。

目前提供的是本机 stdio MCP 服务，没有 HTTP 接口。[ChatGPT 网页版 / Tunnel 接入方案](docs/chatgpt-integration.md)还没有完成整套连接测试。

开发者可以使用[任务编排库](docs/agent-orchestration.md)，组织不同模型和执行器的任务。

## 按需添加组件

- **教材搜索**：另行安装 Node.js 和 QMD，在本地配置中指定资料目录、集合和索引位置。
- **扫描件识别**：安装 Tesseract 和所需语言数据，再安装 Python 扩展：

  ```shell
  uv sync --project . --extra pdf-ocr --no-editable
  ```

- **聊天记录与长期记忆**：分别配置数据存储和权限，参见[历史导入](docs/history-source-ingestion.md)和[Memory 使用说明](docs/memory-mvp.md)。安装本身不会自动导入记录。

## 资料和隐私

CogniVault 只访问你配置的数据位置，不会自动扫描个人目录或上传资料。原始文件与识别出来的文字、AI 分析分开保存，保留来源，方便回到原文核对。

私有配置、数据库、日志和个人资料不应提交到 Git。如果客户端使用云端模型，返回给它的检索内容也会进入相应的对话上下文。数据目录和权限的具体说明见[隐私说明](docs/privacy-boundary.md)。

<a id="upgrade-from-the-former-distribution"></a>

## 从旧包升级

如果以前安装的是 `chatgpt-study-system-v2`，建议使用新环境。沿用原环境时，先卸载旧包，再安装 `cognivault`。以 Windows 的项目环境为例：

```powershell
uv pip uninstall --python .venv/Scripts/python.exe chatgpt-study-system-v2
uv sync --project . --no-editable
```

两个包共用兼容入口和 `study-migrate` 命令，顺序弄反会误删新包的这些文件。如果已经发生，可运行下面的命令恢复：

```shell
uv sync --project . --no-editable --reinstall-package cognivault
```

客户端注册名和启动模块也要改为 `cognivault` 与 `cognivault.transports.mcp_stdio`，原有数据路径和权限继续保留。旧模块 `chatgpt_study_system.transports.mcp_stdio` 仅作为启动兼容入口保留，其他旧 Python API 不再支持；自定义客户端配置的修改方法见各自的接入说明。

## 版本与文档

当前正式版本是 [v0.9.1](https://github.com/abdullejan25-max/cognivault/releases/tag/v0.9.1)，修复了并发保存记忆时的偶发报错。完整变更见[发行说明](docs/releases/v0.9.1.md)。

- [项目进展与已知限制](docs/current-state.md)
- [架构说明](docs/architecture.md)
- [历史记录的整理方式](docs/history-normalization.md)
- [备份与恢复](docs/p13-recovery.md)

## 许可证

项目使用 [Apache-2.0](LICENSE) 许可证。可选的文字识别组件有单独的许可要求，启用前请阅读[依赖许可说明](docs/license-audit.md)。

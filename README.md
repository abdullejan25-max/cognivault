<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/cognivault-icon.png">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/cognivault-logo.png">
    <img src="docs/assets/cognivault-logo.png" alt="CogniVault brand mark" width="300">
  </picture>
</p>

<h1 align="center">CogniVault</h1>
<p align="center">A local-first learning and memory layer for AI agents.</p>
<p align="center"><a href="https://github.com/abdullejan25-max/cognivault/actions/workflows/ci.yml?query=branch%3Amain"><img src="https://github.com/abdullejan25-max/cognivault/actions/workflows/ci.yml/badge.svg?branch=main" alt="CI on main"></a></p>

CogniVault connects AI agents to a user-configured local learning and memory layer. Agent Hosts interpret requests and choose tools; the Gateway validates typed operations, enforces configured capabilities, and records provenance. The core runtime and Gateway do not depend on Codex Desktop.

## Architecture

```text
Agent Host (Codex Desktop, WorkBuddy, Hermes, or another stdio MCP client)
       │ local stdio MCP
       ▼
    Gateway → Services and adapters → Explicitly configured local data
```

**Agent thinks; Gateway executes.** The current transport is local stdio MCP. CogniVault does not provide an HTTP listener or public endpoint.

## What CogniVault manages

- **Study** — Search a user-selected QMD collection. StudyVault remains the authoritative source; search does not replace or rewrite the original materials.
- **Personal History** — Keep source evidence separate from deterministic canonical conversations and messages. Uncertain or unsupported input remains source-only.
- **Wrong Answers** — Preserve the original question evidence and store Agent analysis separately as versioned records.
- **Assets and Documents** — Explicitly register original files and keep extracted text or OCR as derived data linked to its source.
- **Legacy source evidence** — Preserve typed historical input without guessing roles, ordering, timestamps, or conversation boundaries.

## Quick start

### Windows：双击安装（推荐）

下载并解压仓库源码，双击根目录的 **`install.cmd`**。安装入口会自动准备 uv、Python 3.11 和锁定的项目依赖，生成默认只读的本地配置及 Codex 项目配置。无需预装 Python、uv 或 Git，也无需先手工编辑 TOML。

安装完成后，在 Codex 中打开该项目目录、确认信任项目并新建会话，发送：`调用 cognivault 的 health_report，检查当前配置状态。` 安装器显示的完成状态代表基础安装完成；真实客户端连接需要这一步确认。

首次安装默认尚未接入个人资料，Study、History、Assets 均为 `not_configured`。资料搜索、历史查询和错题保存可以按需配置；QMD、OCR 未自动安装。重复运行会保留已有数据位置与权限。其他本机 MCP 客户端及故障重试见[安装说明](docs/installation.md)。

### 手动安装与其他平台

#### Prerequisites

The core runtime needs Python 3.11 or later and [`uv`](https://docs.astral.sh/uv/). It works independently of any particular Agent Host.

#### Install and configure the Gateway

From the repository root, create the private local configuration and install the project. For an existing installation of the former distribution, follow the [upgrade sequence](#upgrade-from-the-former-distribution) before running `uv sync`:

```powershell
Copy-Item config.example.toml config.local.toml
uv sync --project . --no-editable
```

Edit `config.local.toml` outside Git to set the local data locations and capabilities you intend to enable. The example configuration is read-only by default. On Windows, keep `--no-editable`: it installs a wheel and avoids a Python 3.11 editable `.pth` decoding issue when the checkout path contains non-ASCII characters.

An MCP Host starts the Gateway over stdio using the local configuration. The command form is:

```text
uv run --no-sync --project <repository-path> python -m cognivault.transports.mcp_stdio --config <absolute-config-path>
```

Run `uv sync` before the first launch and after changing the checkout or dependencies; `--no-sync` does not install or update dependencies. Host-specific command, argument, environment, and path fields depend on the client.

#### Upgrade from the former distribution

The current Python distribution, import package, and standard MCP registration are `cognivault`. Use a fresh Python environment, or uninstall `chatgpt-study-system-v2` **before** installing `cognivault` in the existing environment. For an existing repository environment on Windows:

```powershell
uv pip uninstall --python .venv/Scripts/python.exe chatgpt-study-system-v2
uv sync --project . --no-editable
```

The two distributions share the deprecated `chatgpt_study_system.transports.mcp_stdio` shim path and the `study-migrate` launcher. Installing the new distribution first and then uninstalling the old one removes both shared files; restore them in the repository environment with `uv sync --project . --no-editable --reinstall-package cognivault` if that sequence already happened. An old regular package can also shadow the new shim in an editable installation. Do not keep both distributions installed.

The old stdio module is a deprecated startup shim that delegates to the single CogniVault Gateway and writes a notice to stderr; other old Python APIs are not supported. Update current Host registrations to `cognivault` and launch `cognivault.transports.mcp_stdio`. The Codex helper migrates only exact recognized helper-generated old configs; custom Codex, Hermes, and WorkBuddy registrations need manual changes. Preserve private backend paths and capabilities. See [Codex setup](docs/codex-host-setup.md), [WorkBuddy setup](docs/workbuddy-host-setup.md), and [Hermes setup](docs/hermes-host-setup.md).

## Agent Hosts and validation

Codex Desktop, WorkBuddy, and Hermes connect to the same Gateway implementation and explicitly configured data layer. Host evidence is time-scoped: pre-rename Desktop, WorkBuddy, and Hermes checks are historical; the post-rename Codex CLI read-only checks are recorded in Current State.

| Host | Project validation record | Setup or evidence |
| --- | --- | --- |
| Codex | Historical Desktop native MCP read: **PASS**; post-rename fresh CLI read-only integrity checks: **PASS** | [Host setup](docs/codex-host-setup.md) · [Current State](docs/current-state.md) |
| WorkBuddy | P12 integration: **PASS**; P13 History GUI verification: **DEFERRED** | [Host setup](docs/workbuddy-host-setup.md) · [P12 checkpoint](docs/p12-step2-workbuddy-checkpoint.md) · [Current State](docs/current-state.md) |
| Hermes | Native MCP History read / no-result checks: **PASS** | [Host setup](docs/hermes-host-setup.md) · [Current State](docs/current-state.md) |
| Other local stdio MCP clients | Protocol-compatible; not individually validated by this project | Follow the client’s stdio MCP configuration instructions. |

`.codex/setup_mcp.py` is only a Codex Desktop Host setup helper. It generates the Git-ignored `.codex/config.toml`; it is not part of the core installation or a requirement for other Hosts.

## Current stable release

The current stable release is **[v0.8.0 — CogniVault Identity & Reliability](docs/releases/v0.8.0.md)**. It publishes the identity transition, CI, and integrity verifier correction. The following acquired-data closure belongs to the historical v0.7.0 completion checkpoint; it is not new acquisition performed by v0.8.0:

| Measure | Result |
| --- | ---: |
| Sources / outcomes | 1,804 |
| Canonical conversations | 487 |
| Distinct canonical messages | 7,334 |
| Wholly source-only records | 1,351 |
| Bounded V1 original inputs | 443 |
| V1-only unknown | 0 |

See [Current State](docs/current-state.md) for Host, acquisition, and recovery details.

## Optional Components

- **Study search:** Install Node.js and QMD separately, then configure their executable, collection, and index locations in the private config. They are not bundled with CogniVault.
- **OCR:** Only for scanned pages or PDFs without a usable text layer. Install Tesseract and the required language data, then enable the optional Python extra with `uv sync --project . --extra pdf-ocr --no-editable`. OCR output is derived text and does not replace the source page.
- **ChatGPT Web / Secure MCP Tunnel:** **OPTIONAL / DEFERRED BY OWNER CHOICE**. The [technical guide](docs/chatgpt-integration.md) remains available; live tunnel E2E is unverified and is not a v0.8.0 release gate.

## Current Limitations

- CogniVault's current transport is local stdio MCP. ChatGPT Web / Secure MCP Tunnel integration is optional and deferred by owner choice.
- The official ChatGPT export remains `acquisition_pending`.
- WorkBuddy P13 History GUI verification remains **DEFERRED**; this does not change its P12 integration result.
- Caller and Agent identity is `reported / unverified`, not authenticated identity.

## Local data and privacy

StudyVault is the sole authoritative source for Study. Private configuration, databases, logs, exports, and personal learning materials stay outside Git. CogniVault does not automatically scan personal directories, import chat history, or upload StudyVault data. Agent-facing reads and writes pass through the configured Gateway and its capability checks; original evidence remains distinct from derived History, document text, and versioned analysis. Installing dependencies downloads software packages from the configured package source, not personal study data. `.gitignore` helps prevent accidental commits but is not a security boundary.

See the [Privacy Boundary](docs/privacy-boundary.md) for data and capability details.

## License

CogniVault is licensed under [Apache-2.0](LICENSE). The optional `pdf-ocr` extra includes PyMuPDF, which has separate AGPL or commercial licensing terms; see the [license audit](docs/license-audit.md) before enabling it.

## Documentation

- [Current State](docs/current-state.md) — Current release, Host, acquisition, and recovery status.
- [Architecture](docs/architecture.md) — Stable components, data boundaries, and extension points.
- [History source ingestion](docs/history-source-ingestion.md) and [canonical normalization](docs/history-normalization.md).
- [Recovery](docs/p13-recovery.md).
- Historical checkpoints: [P11](docs/p11-real-migration-completion.md), [P12](docs/p12-step4-cross-agent-checkpoint.md), and [P13](docs/p13-history-completion-checkpoint.md). Their original project names, commands, Host aliases, and figures are time-scoped evidence; current setup instructions are above.
- Current repository: [abdullejan25-max/cognivault](https://github.com/abdullejan25-max/cognivault).

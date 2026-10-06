# CogniVault Installation Wizard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the existing isolated worktree. Steps use checkbox syntax for tracking.

**Goal:** Windows 用户双击即可安装基础环境并生成可靠的 Codex 项目配置。

**Architecture:** CMD 入口选择可用 PowerShell；PowerShell 引导 uv 与 Python；Python 配置程序复用现有 Codex helper。Gateway 支持显式的未配置 Study 状态，安装器仅处理软件与配置。

**Tech Stack:** Windows CMD、PowerShell 7 / 5.1 兼容引导、Python 3.11+、uv、pytest。

执行记录：2026-10-06 已在现有 detached isolated worktree 完成。最终完整合成测试为 875 passed / 12 skipped；最终相关回归 37 passed，包含 8 个 Windows 入口测试。真实 PowerShell 7 与 5.1 临时安装、安装后 synthetic MCP health/workflow、重复安装以及包审计通过。详见 `docs/install-wizard-checkpoint.md`。

后续授权：用户于同日要求“github更新”，进入 GitHub 推送、PR、CI 与集成阶段；以下不 push 的约束记录的是此前实现阶段，不限制这次明确授权。正式 v0.8.0 标签和 Release 保持原状。

## Global Constraints

- 默认 `read` 权限；不访问 production 数据，不打开或创建业务数据库。
- 已有 `config.local.toml` 原始字节保留；未知 Codex 配置不覆盖。
- `uv sync --locked --no-editable`；不依赖客户端重新继承 PATH。
- 中文、空格路径；明确区分安装完成和真实 Host 验收。
- Windows + Codex 自动配置；QMD、OCR、其他客户端按现有文档配置。
- 在当前 isolated worktree 实施；本轮不 push、不发布新版本。

## Task 1: Gateway 未配置 Study 与 Codex 绝对 uv 路径

**Files:** `src/cognivault/runtime.py`, `.codex/setup_mcp.py`, `tests/test_install_setup.py`, `tests/test_codex_project_bootstrap.py`。

**Interfaces:** `load_gateway_from_config(config_file)` 接受 `[study] backend="not_configured"`；`setup_mcp.py --uv <absolute executable>` 保留无参数行为。

- [x] 写失败测试：未配置 Study 的真实 Gateway health 与 search 错误；拒绝混合 disabled/root 配置；helper 用绝对 uv 路径生成、重复运行与移动后重生成配置。
  ```python
  assert gateway.health_report()['study']['configured'] is False
  with pytest.raises(GatewayError) as error:
      gateway.search_study('synthetic question')
  assert error.value.code == 'STUDY_UNAVAILABLE'
  ```
- [x] 运行 `uv run --extra dev --no-editable pytest -q tests/test_install_setup.py tests/test_codex_project_bootstrap.py`，确认新要求失败。
- [x] 在 runtime 将严格的 disabled Study 配置映射到 `study_root=None`, `study_backend=None`, `qmd_discoverable=False`。helper 增加 `--uv` 参数并在精确识别原生成配置时兼容绝对 executable。
- [x] 重新运行上述测试及现有 History/Documents runtime 测试。

## Task 2: 双击入口与无需手工编辑的基础配置

**Files:** `install.cmd`, `scripts/install.ps1`, `scripts/setup_local.py`, `tests/test_install_setup.py`, `tests/test_windows_installer.py`。

**Interfaces:** `setup_local.py --uv <absolute executable> [--host codex|none]`；PowerShell `-NonInteractive` 用于 CI，不影响默认完成行为。

- [x] 写失败测试：新安装输出可解析且版本与 pyproject 一致的默认只读 TOML；existing config 原始字节保留；语法错误与未知 Host 配置退出失败且无成功提示。
  ```python
  assert config['permissions']['capabilities'] == ['read']
  assert config['study'] == {'backend': 'not_configured'}
  assert config['history'] == {'backend': 'not_configured'}
  assert config['assets'] == {'backend': 'not_configured'}
  ```
- [x] 实现 Python 配置程序，使用 exclusive create，失败保留现有文件；正式 helper subprocess 有超时，Host 选择仅影响是否生成 Codex 配置。
- [x] 写 Windows 实际进程测试：从不同 cwd 调用 Unicode checkout 的 PowerShell 入口；以临时 stub 只替代 uv 下载/安装这一外部边界，断言 locked sync 参数、失败停止、配置生成与退出码。stub 对参数作严格校验，配置与 helper 使用真实代码。
- [x] 实现 PowerShell：优先复用 uv；否则将官方固定版本安装到当前用户工具目录；不修改全局 PATH。uv 自动准备 Python 3.11，locked sync 后调用虚拟环境 Python 的配置程序。所有命令超时与非零结果停止后续阶段。
- [x] 实现 CMD：优先 PATH 中的 pwsh；仅未安装时使用 5.1 兼容引导；退出码保存，双击窗口保留完成提示，非交互模式不等待输入。
- [x] 运行上述测试；使用 temporary fresh checkout 做真实 uv sync 与配置生成，保持 production 未接入。

## Task 3: 文档、CI、包与独立审阅

**Files:** `README.md`, `docs/installation.md`, `docs/codex-host-setup.md`, `.github/workflows/ci.yml`, 本计划与 checkpoint。

- [x] 更新 README 的 Windows 首选路径，保留其他平台命令；记录无需手改配置的默认行为、可选功能、失败重试和 Host 信任步骤。
- [x] Windows CI 加入 installer/runtime 测试。安装器文件进入 sdist，临时配置与日志仍被排除。
- [x] 运行完整合成测试、`uv lock --check`、wheel/sdist build 与 `scripts/verify_package_contents.py`，审查 diff 和私人路径。
- [x] 按 requesting-code-review 技能派发只读 reviewer；修正重要问题，并仅重跑受新改动影响的验证。
- [x] 写 checkpoint，明确 Windows 入口与真实 uv 安装证据、尚未验证的普通 Desktop Host 信任流程。
- [x] 仅提交已审核的当前任务文件；不发布、不声称 GitHub 已有此安装器。

# Codex Host Setup

Windows 新用户可以先双击根目录 `install.cmd`，自动准备运行环境、默认只读 profile 与本机 Codex MCP 配置，之后打开并信任项目、启动新会话。详见[安装说明](installation.md)。安装器调用本页的同一个 helper，并传入 uv 的绝对路径，以免客户端尚未继承新的 PATH。无参数 helper 的原有用法继续支持。

Codex Desktop loads project-scoped `.codex/` configuration only after the project is trusted. The project keeps a portable template at `.codex/config.example.toml`; a one-time setup generates the machine-local `.codex/config.toml` that Desktop reads. The generated file is ignored by Git because its `cwd`, `uv --project`, `PYTHONPATH`, and Gateway config paths point to this checkout. Codex supports `env` for stdio servers; the generated `PYTHONPATH` points directly at this checkout's `src` so module discovery does not depend on the Host resolving `cwd = "."` or a locale-sensitive editable-install path.

The root `config.local.toml` is separate: it configures the Gateway's private Study, QMD, History, and Asset locations. Neither setup file belongs in a public commit. The setup script never chooses a personal data directory or migrates data. The current generated registration is `[mcp_servers.cognivault]`, launching `python -m cognivault.transports.mcp_stdio`.

## Upgrade an existing installation

Use a fresh environment, or uninstall `chatgpt-study-system-v2` before installing `cognivault`, following the [README upgrade sequence](../README.md#upgrade-from-the-former-distribution). The old and new distributions overlap at the deprecated stdio shim and `study-migrate` launcher; uninstalling the old package after installing the new one deletes those files. An old regular package may also shadow the new editable shim.

After installing the canonical package, rerun `python .codex/setup_mcp.py`. The helper replaces only exact recognized configurations it generated under the old `study_system` registration, including the supported earlier launcher forms and moved checkout paths. It removes the old registration in the same atomic config replacement. It preserves and refuses unknown user edits, extra servers, and configs containing both old and new Gateway registrations. For a custom config, manually change the registration to `cognivault` and module to `cognivault.transports.mcp_stdio`, preserving backend profiles, capabilities, and unrelated servers. Do not create a second registration for the same Gateway.

## One-time setup

1. Install Python 3.11 or newer and `uv`. Ensure `uv` is on the `PATH` inherited by Codex Desktop. If you changed `PATH` after opening Desktop, restart Desktop so its MCP process inherits the updated environment.
2. If it does not already exist, copy the root `config.example.toml` to `config.local.toml`. Edit only that ignored local file to configure the intended private Study/QMD locations and any History or Asset capabilities you explicitly want. The example is read-only by default and contains placeholders; setup does not enable private writes for you.
3. From the repository root, run `uv sync --project . --no-editable` to install the locked runtime dependencies as a wheel. This avoids Python 3.11 on Windows decoding a UTF-8 editable `.pth` path with the active legacy code page when the checkout path contains non-ASCII characters. If you plan to run tests, use `uv sync --project . --extra dev --no-editable` instead.
4. From the repository root, run `python .codex/setup_mcp.py`. The script locates the checkout from its own file path, validates that `config.local.toml` is readable TOML and that `uv` is available, then writes `.codex/config.toml` with explicit local paths. It works when the checkout path contains spaces or non-ASCII characters. Running it again leaves a compatible local configuration unchanged.
5. Open this exact repository in Codex Desktop and trust it when prompted. Start a new conversation in the repository so Desktop loads its project MCP configuration. The generated launcher uses `uv run --no-sync`, so repeat `uv sync` after changing the checkout or its dependencies.

The setup script refuses to overwrite an unrecognized `.codex/config.toml`; inspect and preserve that file before deciding how to proceed. A compatible canonical configuration stays unchanged; exact recognized old generated configurations are migrated as described above. `required = true` remains enabled: a real MCP startup failure is visible to Desktop instead of silently removing the server. The generated `env` field supplies the checkout's source path.

## Verify

From a trusted project, confirm `cognivault` appears in a newly created Codex Desktop conversation. Call `health_report` first and confirm it reports the configured local service state before attempting any persistent operation. Read the known `study-workflow://wrong-answer` resource directly if resource listing fails. Then, if desired, run the repository's synthetic tests with `uv run --project . --extra dev pytest -q`.

An inline MCP override or a synthetic Host test does not prove that a normal Codex Desktop project session loaded `.codex/config.toml`. A clean-clone release check should begin with the README, configure only disposable/synthetic local paths, run setup and tests, and verify MCP startup plus `health_report` in a new trusted Desktop conversation.

Earlier Desktop PASS results retain their original names in the historical checkpoints. The renamed build needs its own actual Host and existing-data Gateway checks; those are not established by this setup guide.

## Isolated Desktop acceptance

From the installed development environment, run `python scripts/prepare_desktop_acceptance.py --root <empty-directory-outside-Git>` to prepare invented data and separate writer/reader projects. Existing nonempty directories are refused, preserving earlier evidence. The generated `DESKTOP-ACCEPTANCE.md` explains project trust and all six Gates; preparation and SDK preflight remain distinct from real Desktop receipts.

Open each exact writer/reader folder, trust it if prompted, and create a new **Local** task. Submit the whole `prompts/00-mcp-startup.txt` before checking `/mcp`. This diagnostic permits only one `cognivault_synthetic.health_report` call; unavailable tools must stop it. Opening or trusting a folder alone does not establish that a new task loaded its configuration. The diagnostic is separate from the six acceptance Gates.

The Desktop build inspected on 2026-10-11 queried `/mcp` by Host, without a task ID or working directory, and cached that status for five minutes. Its MCP settings read configuration for the selected local project. These are different contexts: an old Host status entry does not prove a new project's startup failed. Use the new task's actual available tools and native call receipt to establish loading; preserve an actual startup error if one occurs. This observed UI behavior is version-specific.

Synthetic configs explicitly enable `cognivault_synthetic` and disable the known `cognivault` and `study_system` stdio registrations, with all three launch definitions pointing at the isolated profile. Global configuration and Host trust are untouched. This protects the checked stdio configuration; it is not a universal override for arbitrary aliases or higher-priority flags. A conflicting inherited HTTP `url` can make the Host reject a stdio config before startup. Report that conflict and preserve configuration evidence rather than altering a production registration to make the test run.

For a CLI-only configuration diagnosis, run `codex mcp get cognivault_synthetic --json` with the shell's actual working directory set to the exact synthetic project. In the inspected CLI build, passing global `-C` from another directory did not select that project's configuration for the `mcp` subcommand. Configuration output is not Desktop acceptance, and a temporary trust override is not evidence that Desktop trusted the project.

Copy each generated `prompts/*.txt` file in full as one Desktop message. Revision, reader permission checks and restart reads carry the same complete `memory_id`, `subject` and `predicate`, also recorded in `acceptance-manifest.json`. For a revision, fetch that exact ID, check its identity, and pass the returned `target_guard` unchanged. An incomplete ID must stop a write; matching a source reference is insufficient to choose another target because several facts can share one source. Read-only `fetch_memory_request` checks the idempotency receipt without replaying a write.

Record actual tool arguments/results and task IDs. A reader with no discoverable write tool proves discovery isolation, while `PERMISSION_DENIED` proves an attempted call was rejected; report the observed case. New conversations alone do not prove an application restart. If Desktop cannot legally be restarted automatically, preserve that Gate as BLOCKED for one focused manual action. Never relabel CLI or SDK results as Desktop acceptance.

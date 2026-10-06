# CogniVault 安装向导设计

日期：2026-10-06。状态：用户在方案后要求“尽量做到省心一点”，按上述第一版范围实施。

## 目标与范围

让 Windows 新用户下载并解压源码后，双击 `install.cmd` 即可进入中文安装向导。向导负责准备运行环境、安装当前源码对应的锁定依赖、生成本机配置，并给出接入 Codex 的最后一步。基础安装不要求用户预装 Python、uv 或 Git。

“一键”指一个安装入口；用户仍需自行选择是否接入个人资料、是否开放写入，以及在客户端确认项目信任。安装完成、领域功能可用和真实客户端验收分别显示。

第一版以 Windows + Codex 为自动配置路径。其他本机 MCP 客户端得到可复制的启动参数；不自动合并 Hermes、WorkBuddy 的用户级或 managed 配置。macOS/Linux 继续保留现有安装方式。

## 方案比较

| 方案 | 优点 | 代价与限制 |
| --- | --- | --- |
| Windows 双击入口 + Python 向导（推荐） | 复用现有 Gateway 和 Codex helper，依赖少，容易测试和维护 | 下载源码后启动；客户端信任需用户确认 |
| 打包完整桌面安装程序 | 可以提供图形界面与捆绑运行时 | 增加构建、签名、平台和升级维护成本 |
| 跨平台命令行入口 | 多平台逻辑一致 | 新用户仍需找到终端执行命令，不满足 Windows 双击体验 |

## 安装流程

1. 检查源码包是否完整，按入口文件的位置定位仓库，不依赖用户当前目录。缺少锁文件、配置模板或核心源码时退出并给出重新下载说明。
2. 优先使用 PATH 中的 PowerShell 7。未安装时仅使用 Windows 内置 PowerShell 5.1 执行兼容的依赖引导；这是未预装 PowerShell 7 的 Windows 双击入口的兼容性需求。运行时和配置向导由 Python 执行。
3. 检测可用 uv；缺失时通过 Astral 官方安装渠道安装到当前用户可写位置，并将绝对路径传给后续步骤。使用 uv 准备项目兼容的 Python 3.11 运行时。安装不依赖管理员权限，不修改全局 Python 环境，也不卸载旧软件。
4. 在项目环境中执行 `uv sync --locked --no-editable`。下载失败或安装失败立即停止，显示失败阶段、可重新运行的入口和简短修复说明，不显示成功结论。
5. 首次安装生成被 Git 忽略的 `config.local.toml`。默认权限为 `read`，History、Assets、Study 均可处于明确的未配置状态。默认安装不写入假资料目录，也不把缺少搜索组件解释成已能检索。
6. 复用 `.codex/setup_mcp.py` 的配置识别和保护规则，为当前仓库生成项目级 Codex MCP 配置。使用可靠的 uv 绝对路径，避免已经打开的客户端尚未继承新 PATH 而无法启动。保留 `required = true`。
7. 安装器提供环境检查；若用户选择检测领域后端，要求在受信任的正常客户端会话中调用正式 `health_report`。安装器不直接查询 SQLite 或私有 store，不通过临时 MCP overrides 或独立客户端绕过项目接入。
8. 最后显示：基础环境是否安装完成、哪些功能尚未配置、Codex 中打开哪个项目并确认信任，以及在新会话中调用 `health_report` 的操作。只有实际客户端调用通过后才能报告该客户端验收成功。

## 可选资料接入

向导允许用户明确选择自己的 Study 目录，也允许暂时跳过。Study 搜索仍需要受支持的 QMD 2.8.3 与 Node.js；本次基础安装不捆绑 QMD、OCR，也不扫描或自动索引个人目录。需要搜索时，向导列出缺失项并链接对应配置说明。

History、Assets 的现有后端位置与写入权限属于高级配置。本次不自动创建业务数据库，不自动导入聊天或资料。需要错题保存时提示另行启用对应后端和 `write` 能力，不将基础安装成功表述为错题保存已可用。

为支持诚实的“稍后配置资料”，新增 `[study] backend = "not_configured"` 配置形式。旧 Study/QMD 配置保持兼容；未配置状态下 `health_report` 返回 `study.configured = false`，Study 搜索返回现有 `STUDY_UNAVAILABLE` 错误。

## 重复运行与现有安装

- 已有 `config.local.toml` 时保留其原始字节和 capabilities，不重写或猜测用户的数据路径。配置无效时停止并提示人工修正。
- Codex helper 只更新精确识别的自有生成配置；未知配置保留并报告冲突，不覆盖其他服务器。
- 向导不会修改全局客户端配置、项目 trust 设置或用户级 execution policy。
- 网络和进程操作有超时，退出码可靠传递；取消操作保留已有数据和配置。
- 未完成的安装可以重新运行；不把部分完成的阶段打印成全部安装成功。
- 当前开发验证仅使用临时目录与手工合成配置，不接入这台机器的 production 数据。

## 预计修改位置

- 新增根目录 `install.cmd`：Windows 双击入口和退出状态。
- 新增 `scripts/install.ps1`：运行环境检测与官方 uv 引导。
- 新增 `scripts/setup_local.py`：中文向导、本机配置生成和阶段状态。
- 修改 `.codex/setup_mcp.py`：支持安装器提供的 uv 绝对路径，保留现有识别、幂等和配置保护行为。
- 修改 `src/cognivault/runtime.py`：使用 Gateway 已支持的空 Study backend，支持明确的未配置 Study 状态，维持既有配置兼容。
- 修改 README、Codex setup 文档，新增安装向导说明与 Windows CI 验证入口。

## 验收要求

1. 在临时中文、空格路径下从另一工作目录启动，正确生成配置和启动参数。
2. 默认未配置 Study/History/Assets 的 Gateway 能按正式配置启动；领域操作明确返回不可用，不返回伪造的空成功。
3. uv 不在 PATH、Python 不在 PATH、网络失败、进程失败、用户取消均有明确阶段输出和非零失败退出码。
4. 重复安装不改已有 private 配置；自定义 Codex 配置冲突时原文件字节不变。
5. Windows PowerShell 7 的实际入口运行验证；5.1 兼容分支仅在具备对应环境时实测，否则明确标为未验证。
6. 新旧 bootstrap 与 runtime 测试通过，完整合成测试和 wheel/sdist 内容审计通过，安装产物不包含本机配置或私人路径。
7. 全新普通 Codex 会话的 Host 验收独立记录；自动测试、配置生成和安装器退出码不作为其替代证据。

## 已核对依据

当前工作树基于正式 v0.8.0 commit `748ef70e7ac29bde3ce6c01d04333ac0ee9e5526`，开始时无本地变更。现有 helper 只生成 Codex 配置；runtime 当前要求 Study root 与 QMD 配置。私有数据访问边界来自仓库 AGENTS.md 与 `.codebuddy/rules/study-system-gateway-only.md`。

- [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)
- [uv 管理 Python](https://docs.astral.sh/uv/guides/install-python/)
- [官方 MCP 配置说明](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)：项目级配置仅适用于 trusted projects。

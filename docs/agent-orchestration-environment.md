# Agent 编排：真实环境证据

核实日期：2026-10-24。基线提交：`a8f12d575ea4f3818dd9d83aca79b7c75df0bba5`。

本报告只证明下表中逐项写明的能力。安装文件、CLI 帮助、可用认证、模型调用、Gateway 连通和完整 Host 验收是不同证据，不能互相替代。

## 状态定义

- `CONFIGURED`：找到了安装或配置元数据；不代表服务已连接。
- `CONNECTED`：当前 Host 或协议连接已实际成功；不代表任务执行通过。
- `VERIFIED`：指定范围的检查实际运行且通过；必须同时说明检查范围。
- `BLOCKED`：当前目标缺少必要条件或未获所需连接，不能宣称完成。

## 本机证据矩阵

| 对象 | 状态 | 本次证据 | 证据边界 |
| --- | --- | --- | --- |
| Codex CLI | `CONFIGURED` / `VERIFIED`（帮助与版本） | PATH 可解析；`codex --version` 为 `codex-cli 0.164.0-alpha.11.1`；`codex exec --help` 成功 | 没有启动新的 Codex 模型任务，没有验证认证或额度 |
| Codex 非交互接口 | `VERIFIED`（参数存在） | 本机帮助列出 `exec`、stdin `-`、`--json`、`--output-schema`、`--ephemeral`、`--sandbox read-only`、`--output-last-message` | 参数存在不等于真实模型运行成功；权限限制仍须由执行器和 Host 实施 |
| Codex 作为 MCP server | `BLOCKED`（未证实可用） | `codex mcp-server --help` 返回根帮助；根子命令列表没有 `mcp-server` | 即使退出码为 0，也不是 MCP server 能力通过的证据；不能套用旧版 Cookbook |
| DeepSeek Harness Desktop | `CONFIGURED` | `%LOCALAPPDATA%/Programs/DeepSeek Harness/DeepSeek Harness.exe` 存在；PE `ProductName=DeepSeek Harness`，`FileVersion=0.2.0-rc.2`；安装归档中包名为 `@deepseek-ai/dsh-desktop`，版本相同 | 安装证据不证明登录状态、选用模型或 Gateway 授权 |
| DeepSeek Harness CLI | `VERIFIED`（帮助与版本） | 安装携带的 `resources/runtime/cli/bin/dsh.cmd --version` 返回 `0.2.0-rc.2`；同入口 `--help` 成功；两次退出码均为 0 | 使用安装自带程序文件，没有运行 profile 或读取用户 `.dsh` 数据 |
| DeepSeek Harness 运行进程 | `CONFIGURED`（进程存在） | 查询进程名时观察到 `DeepSeek Harness` 进程 | 未读取命令行、会话、端口凭据或用户运行数据；进程存在不等于编排层已连接 |
| DeepSeek Harness headless / ACP / SDK | `CONFIGURED`（代码与文档接口） / `BLOCKED`（实际任务） | 本地 CLI 帮助包含 headless 示例；安装包元数据对应官方 `deepseek-ai/deepseek-harness`；官方文档描述 headless JSONL、ACP stdio、SDK stdio JSON-RPC | 没有启动这些 profile，没有发送真实模型任务；尚无 `CONNECTED` 证据 |
| Hermes | `BLOCKED`（当前安装入口未确认） | PATH 无 `hermes`；有限常用位置无匹配；默认 `.hermes` 和 `~/hermes-agent` 不存在 | 不能据此断言未安装；没有扫描其它磁盘、WSL、容器、用户会话或自定义 `HERMES_HOME` |
| 当前工作树的 CogniVault 配置 | `BLOCKED` | 本工作树 `.codex/config.toml` 不存在；用户 Codex 配置的 MCP server 名只见 `node_repl`；没有当前工作树的 exact project trust 条目 | 未改变 trust 或 MCP 配置；未输出其它配置值；不存在 exact 条目不等于已证实“不受信任” |
| 当前 Desktop Host 的 CogniVault | `BLOCKED` | 主调查通过 Host 直接读取已知 workflow URI 时得到 `unknown MCP server 'cognivault'` | 不能用独立 CLI、临时覆盖或直接数据库客户端替代正式 Gateway；本报告未读取私有 store |
| 基础工具 | `VERIFIED`（PATH 发现） | `python`、`uv`、`gh`、`pwsh` 可解析 | 不等于对应外部服务已登录或网络连接可用 |

## 已确认的 DeepSeek Harness 入口

`deepseek`、`deepseek-h`、`hermes`、`dsh` 均未从当前 PATH 解析。进一步检查安装根后，确认有可用的自带 `dsh.cmd`：

```text
%LOCALAPPDATA%/Programs/DeepSeek Harness/resources/runtime/cli/bin/dsh.cmd
```

该脚本将 `ELECTRON_RUN_AS_NODE=1` 限定在其子进程环境，然后使用安装中的 Electron 和 `@deepseek-ai/dsh-desktop-host/lib/cli.js` 启动普通 dsh CLI。因此无需下载另一套包或修改 PATH，就能配置显式绝对路径入口。

本次先检查安装归档内 `dsh-desktop-host/lib/cli.js` 和 `dsh/lib/bin.js`：根 `--version` / `--help` 在 profile 启动和 `loadLayeredEnv("dsh")` 前退出，随后才运行这两个只读检查。没有直接尝试 GUI executable 的未知 `--help` 行为。

官方 CLI 文档提供以下候选能力；本机本次只核实根帮助与版本，以下属于下一阶段的接口候选：

| 模式 | 文档说明 | 编排适用性 |
| --- | --- | --- |
| `dsh headless` | 单任务输出后退出；`-` 从 stdin 读取；`--json` 输出运行事件；可指定权限 preset 和审批 policy | 适合受控 subprocess 适配器；需要解析真实事件、超时和取消 |
| `dsh acp` | 通过 stdio 提供 Agent Client Protocol（Agent 与客户端交换任务和事件的协议） | 适合有生命周期与权限协商的客户端；需单独协议实现和验收 |
| `dsh sdk` / `sdk-minimal` | 提供 stdio JSON-RPC | 适合官方 SDK；不能把它直接当作 MCP server |

`dsh <profile> --help` 会进入 profile 启动路径，不能认为它与根 `dsh --help` 一样不接触用户配置。本次未运行 profile 帮助、配置 dump、登录、模型任务或连接既有 Desktop 会话。

## 真实外部调用仍需满足的条件

1. 给编排执行器配置已确认的 CLI 入口及独立参数数组。不要把完整命令当 shell 字符串拼接；从 stdin 传送有明确外发范围的任务。
2. 明确合成任务、允许目录、模型调用和输出落点。调用方输入不能自动携带 Study/History/Memory、源文件、会话记录或凭据；学习系统材料仍只能由 Gateway 按授权提供。
3. 对 DeepSeek profile，先建立独立、可审查的 `DSH_HOME` / profile 和认证接入方式，再执行协议启动检查。不能默认复用用户私有 `.dsh`，也不能为获得登录态而读取凭据文件。隔离 home 是否能使用现有认证仍未验证。
4. 运行真实合成任务，保存经脱敏的退出码、事件、最终结果、超时/取消和权限拒绝证据；才可将对应调用提升为 `CONNECTED` / `VERIFIED`。
5. 对 CogniVault，先让当前工作树在 Desktop 中获得正式 MCP 配置与信任，再用当前 Host 读取已知 workflow 并调用正式工具。配置存在、CLI 退出码为 0、历史验收记录均不能代替本次 Desktop Host 验收。

本次没有外部模型调用、Gateway 私有数据访问、认证值读取、微信扫描、服务启动或配置修改。安装目录 `resources/runtime` 是随程序发布的代码资源；它与用户 profile、会话、store 和运行状态目录有区别，本次只读取前者的必要入口代码。

## 调查方法与来源

本地检查限于 `Get-Command`，指定安装根与有限常用软件目录的浅层名称，进程名称，指定 executable 的 PE 版本，安装 ASAR 的包元数据和入口代码，以及 Codex 配置中 MCP server 名和当前工作树的 exact trust 匹配。配置不输出 command、args、env、URL、token 或其它值。

有限安装位置为 `%LOCALAPPDATA%/Programs`、`Program Files`、`Program Files (x86)`、`~/.local/bin`、uv tools 默认目录，以及已知 Hermes 默认目录。历史 Hermes 记录仅用于选择有限检查位置，所有当前状态结论均来自本次检查。

以下官方页面于核实日期实际搜索并打开；远端 master 文档可继续变化，不代表本机版本全部支持：

- [OpenAI：非交互模式](https://learn.chatgpt.com/docs/non-interactive-mode)：`codex exec`、JSONL 和 JSON Schema 输出。
- [OpenAI：开发命令](https://learn.chatgpt.com/docs/developer-commands?surface=cli)：当前命令参考；以本机 help 核实参数。
- [OpenAI：MCP 配置](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)：项目 `.codex/config.toml` 只对可信项目生效。
- [DeepSeek 官方 Harness 页面](https://www.deepseek.com/en/harness/)：官方项目身份与仓库入口。
- [DeepSeek 官方 CLI 帮助](https://github.com/deepseek-ai/deepseek-harness/blob/master/docs/cli-help.md)：headless、ACP 与 SDK 模式。
- [DeepSeek 官方 Desktop 文档](https://github.com/deepseek-ai/deepseek-harness/blob/master/apps/desktop/README.md)：可选的 Manage dsh Command 注册及 Desktop 携带的 CLI。

# Hermes Host setup

P12 Step 3 以当时本机 Hermes Agent v0.20.0 (2026.8.3) 为调查对象。
原生 stdio 可连接现有 Gateway，不需要 Hermes-specific business adapter。
更名前的真实 Agent 已通过生产只读及隔离写入；具体 Gate 与 Host 范围见 [历史 checkpoint](p12-step3-hermes-checkpoint.md)。这些结果不证明改名后的 live Host 或存量生产数据兼容。
Discovery 本身不能替代 Agent E2E。

## Configuration

以实际 `HERMES_HOME` 定位 `config.yaml`，不要假设一定在 `~/.hermes`。
profile 会改变 home。官方配置 loader 还支持 managed overlay，managed 值可能优先。
native config 同名服务器优先于 portable plugin 定义（后者被跳过）。
P12 当时安装原先无 MCP 定义，无同名 production 覆盖；WorkBuddy 配置未改。

升级先按 [README](../README.md#upgrade-from-the-former-distribution) 使用新环境，或先卸载 `chatgpt-study-system-v2` 再安装 `cognivault`。旧新包共享 stdio shim / `study-migrate` launcher；顺序颠倒会删除共享文件，旧 regular package 也可能遮蔽新 editable shim。Hermes 用户配置不由 Codex helper 迁移：手动将 production registration 改为 `cognivault`，module path 改为 `cognivault.transports.mcp_stdio`；保留 profile、私有路径、capabilities、其他服务器与 P12 isolated aliases。

以下只展示占位符；使用绝对 executable/config/source 路径，并在 Git 外保存实际配置：

```yaml
mcp_servers:
  cognivault:
    command: "C:/PATH/TO/gateway/python.exe"
    args: ["-X", "utf8", "-m", "cognivault.transports.mcp_stdio", "--config", "C:/PATH/TO/production-readonly.toml"]
    env:
      PYTHONPATH: "C:/PATH/TO/checkout/src"
      PYTHONUTF8: "1"
    enabled: true
    timeout: 90
    connect_timeout: 60
    sampling:
      enabled: false
memory:
  memory_enabled: false
  user_profile_enabled: false
```

production-readonly.toml 保留原生产后端，仅 permissions.capabilities 为 `["read"]`。
在原有 Hermes config 上合并以上字段，保留模型、credentials、其他服务器及配置。
先备份；不要将完整用户配置复制进仓库。关闭 memory 是持久用户级设置，影响共享此 home
的 Hermes 会话；本次已按用户 Single-Brain 要求配置并保留私有恢复备份，未删除既有记忆。

当前连接与 discovery 检查使用 `hermes mcp test cognivault`。P12 当时以旧 registration 执行的 `hermes mcp test study_system` 实际发现 15 个 Gateway read tools；native 注册另有 resource/prompt utilities，19 个名称不等于 19 个 Gateway tools。这些是历史范围内的数字，不作为当前工具总量。
fresh process 验证持久配置加载；已有 Desktop 长进程需单独验证 reload。
CLI `/reload-mcp` 与 messaging gateway config watcher 属产品路径，不是本轮实际 Desktop reload 证据。

## Agent boundary

项目 AGENTS.md 对 Hermes 一样生效：读、计数、存在性检查都通过 Gateway。
无结果保持无结果；session_search、filesystem、Memory 不可替代 V2 数据。
缺失 capability 或 logical ref 应报告缺证据，不构造或自行查 private stores。
Hermes 自身 session/runtime metadata 可保留，但不作为 V2 权威层。
本次未配置 external memory provider；未进行知识库或会话导入。

只有 production 真实 Agent read-only/no-result/boundary PASS 后，才能启用
`study_system_p12_hermes_isolated`，其 config、Assets、Wrong Answers、document inbox、Vault
必须独立于生产。重复更新只产生 v1/v2；restart baseline 经 Gateway 返回建立。

## Evidence

1. `hermes mcp test` 是真实产品 CLI transport/discovery。
2. Hermes native registered handler 的 health 调用是 native client/Gateway 证据。
3. 模型发起工具调用并遵循边界才是 Agent E2E。
4. SDK/pytest 是补充证据，不能替代第 3 项或真实 Host 进程退出/重启。
P12 测试对象为真实 Hermes CLI Agent Host；Desktop shell live reload、GUI 重启未单独验收。改名后需在实际 Host 中再次调用 `health_report` 与已配置的只读工具；不以 synthetic stdio 或 CLI exit code 0 代替验收。

官方资料：[MCP guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/mcp/)、
[config reference](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/mcp-config-reference.md)、
[memory guide](https://hermes-agent.nousresearch.com/docs/user-guide/features/memory/)。

## Historical verified model entry

P12 用户成功会话的 Deepseek V4 Pro High 经当时实际 resolver/picker 核实：
provider `deepseek`，model `deepseek-v4-pro`，reasoning `high`，既有 default profile。
High 是推理设置，非独立 model ID。内置 provider 使用官方 API 和既有私有凭证，
不同于先前失败的 custom provider。CLI 可明确选择相同会话入口：

```powershell
hermes --provider deepseek -m deepseek-v4-pro --reasoning high -z '<task>'
```

此命令不改变持久默认模型。GUI 当前会话 selection 可覆盖 picker 的旧 profile default；
不要把某次成功 GUI 会话解释为所有 CLI 默认 provider 都可用。某些 API 失败时 CLI
仍退出码 0，验收必须同时检查实际工具回执和错误输出。

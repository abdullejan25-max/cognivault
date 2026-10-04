# WorkBuddy Host setup

WorkBuddy 使用本机 stdio MCP 连接 CogniVault；Gateway 负责相同的领域操作、权限与 provenance。既有 [P12 checkpoint](p12-step2-workbuddy-checkpoint.md) 和 [跨 Agent checkpoint](p12-step4-cross-agent-checkpoint.md) 记录的是更名前的实际 Host 验收。WorkBuddy P13 History GUI verification 仍为 **DEFERRED**；改名后的 live Host 与存量数据兼容尚未验收。

## Installation and registration

先按 [README 升级步骤](../README.md#upgrade-from-the-former-distribution) 使用新 Python 环境，或先卸载 `chatgpt-study-system-v2` 再安装 `cognivault`。旧新 distribution 共享旧 stdio shim 文件和 `study-migrate` launcher，先安装新包再卸载旧包会删除它们；旧 regular package 也可能遮蔽 editable 安装的 shim。

WorkBuddy 用户/managed MCP 配置不由 `.codex/setup_mcp.py` 迁移。按实际 Host 配置入口手动把原 production registration 改为 `cognivault`，module 改为 `cognivault.transports.mcp_stdio`，保留原私有 backend profile、capabilities、其他 MCP servers 与凭据。需要 owner 操作的 managed entry 按实际 UI 处理；无法编辑或重载时如实报告阻塞，不用临时覆盖替代。

以下是 stdio launcher 的占位参数，实际字段结构按 Host 配置保存于 Git 外：

```json
{
  "command": "C:/PATH/TO/gateway/python.exe",
  "args": ["-X", "utf8", "-m", "cognivault.transports.mcp_stdio", "--config", "C:/PATH/TO/PRIVATE/production-readonly.toml"],
  "env": {
    "PYTHONPATH": "C:/PATH/TO/checkout/src",
    "PYTHONUTF8": "1"
  }
}
```

stdio 只连接一个 CogniVault Gateway；无需 HTTP endpoint。`study-workflow://wrong-answer` 仍是规范错题工作流 URI。`study_system_p12_isolated` 与 `study_system_p12_crossagent` 是已命名的历史 synthetic servers，继续指向原 isolated targets；不可改指 production。

## Actual Host verification

重载配置后启动 fresh WorkBuddy 会话，确认其实际加载 `cognivault`，调用 `health_report`，再经 Gateway 执行已授权的只读查询。读取、计数、存在性检查与写入都必须经 MCP/Gateway；结果为空时保留无结果。错题请求直接读取已知 workflow URI，随后依该正文执行。

P12 的同名 definition 优先级和 Desktop reload 保证未全面建立，故配置文本存在不等于生效。确认实际工具调用与 Host trace 后才能报告本次 PASS。SDK discovery、synthetic tests 或其他 Host 的成功不能替代 WorkBuddy GUI/Agent 证据。持久写入只在用户已授权且 profile 授予能力时执行。

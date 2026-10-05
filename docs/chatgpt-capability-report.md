# ChatGPT 实际接入 Capability Report

> 历史调查报告：下文保留 2026-09-25 的判断与建议。当前 ChatGPT Web / Secure MCP Tunnel 状态为 **OPTIONAL / DEFERRED BY OWNER CHOICE**，没有 live E2E，不属于 v0.8.0 release Gate，也不要求为发布完成 OpenAI 账号授权。当前状态以 [Current State](current-state.md) 为准；可选技术步骤见 [Secure MCP Tunnel 指南](chatgpt-integration.md)。

日期：2026-09-25
状态：需要用户账号与 Platform 决定

## 已验证的本机能力

- Phase 3 的 Codex Host E2E 已通过：真实 stdio MCP 可调用
  `health_report` 和 `search_study`。
- Gateway 是 transport-neutral；没有 HTTP listener、公开 URL、API key、tunnel
  client 或 ChatGPT-specific runtime dependency。
- 真实 StudyVault 只能经 disposable QMD runtime 读取，原始权威数据不由
  Gateway 写入。

## 当前官方接入路径

OpenAI 官方资料说明，ChatGPT developer mode 可以添加 MCP server。私有本机
server 应使用 Secure MCP Tunnel，而不是依赖 `localhost` 或公开端口：

```text
ChatGPT developer-mode Plugin
        ↓
OpenAI Secure MCP Tunnel
        ↓ outbound HTTPS only
tunnel-client on this PC
        ↓ stdio
ChatGPT Study System V2 Gateway
```

该 tunnel 不开放入站端口，也不用于公开 Plugin 发布。它要求：

1. Platform tunnel settings 中已创建并关联到目标 ChatGPT workspace 的 tunnel；
2. 可使用该 tunnel 的 Platform 权限；
3. `tunnel_id`；
4. 仅供 `tunnel-client` 使用的 runtime API key；
5. ChatGPT Settings → Security and login 中可用且已开启 Developer mode。

官方来源：

- [Connect and test your plugin](https://developers.openai.com/plugins/deploy/connect-chatgpt)
- [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

## 为什么当前停止

上述步骤会创建/使用账号资源、需要用户操作与 API key；这些都在项目的自主开发
红线内。当前没有检查或修改 ChatGPT Developer mode、Platform tunnel 或账户权限，
也没有请求、读取或保存 API key。

公开 HTTPS forwarding 也不是自动替代方案：它需要开放公网可达入口，且与本项目的
私有个人数据边界不匹配。

## 用户可选的下一步

推荐：由用户确认使用 Secure MCP Tunnel，并在 Platform 创建/选择一个仅用于本项目
的 tunnel，按安全方式向本机 `tunnel-client` 提供 runtime API key；随后允许继续
Developer mode 与 ChatGPT Plugin 的实际 E2E。

替代：暂不连接 ChatGPT，继续实现 Personal History、错题和 PDF pipeline；V2 仍可
通过 Codex local MCP 开发与测试，但不能声称已完成 ChatGPT-first 的真实产品接入。

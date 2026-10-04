# ChatGPT through OpenAI Secure MCP Tunnel

Checked against the official OpenAI documentation on 2026-10-05. ChatGPT connects to remote MCP apps; it does not connect directly to a local stdio server. For a server kept on a private computer or network, OpenAI documents Secure MCP Tunnel and its `tunnel-client` relay. The relay makes outbound HTTPS connections to OpenAI and starts the MCP command inside the local trust boundary. CogniVault itself remains a local stdio MCP server; this setup does not add an HTTP listener or a second Gateway.

References: [Developer mode and MCP apps in ChatGPT](https://help.openai.com/en/articles/12584461-developer-mode-and-mcp-apps-in-chatgpt), [Secure MCP Tunnel](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels), and the [official tunnel-client guide](https://github.com/openai/tunnel-client/blob/master/README.md).

## Security boundary

- Create a separate local Gateway config for ChatGPT with `[permissions]` set to `capabilities = ["read"]`. Do not point the relay at a config with `write`, `ingest`, `projection`, or `admin` enabled. The MCP server then omits tools that require those capabilities and the Gateway still checks each call.
- Start from `config.example.toml`, configure only the local read sources intended for this connection, and save the result as `config.chatgpt-readonly.local.toml`. That filename is ignored by Git. Do not commit the file or a tunnel profile containing local paths or credentials.
- Tunnel access makes configured read results available to the ChatGPT workspace. Review the Study/History sources and the workspace's data policies before connecting. Read-only prevents CogniVault writes; it does not prevent a connected model from reading data exposed by enabled read tools.
- `tunnel-client` authenticates its connection to OpenAI's tunnel service. It does not authenticate an individual ChatGPT user to CogniVault. Anyone able to use the connected app receives the same Gateway read capabilities. Caller identity remains whatever the Gateway reports; do not treat a tunnel ID or runtime key as a verified user identity.
- Keep `CONTROL_PLANE_API_KEY` in the environment or approved secret manager used to launch `tunnel-client`. Never paste it into this repository, a ChatGPT prompt, or a committed profile. Use the least-privileged runtime key with Tunnels Read + Use; tunnel creation/editing is a separate permission.
- Use the official OpenAI `tunnel-client` download and the current [quickstart](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels#setup-tunnel-client). The client needs outbound HTTPS and local access to the stdio command; no inbound port or public MCP URL is needed. Keep only one active stdio `tunnel-client` process per tunnel ID.

## Local configuration

1. If needed, copy `config.example.toml` to `config.chatgpt-readonly.local.toml`. Set only the local Study/QMD and other read backends you intend ChatGPT to access. Keep this section exactly read-only:

   ```toml
   [permissions]
   capabilities = ["read"]
   ```

2. Install the package in a clean environment or use the checkout's existing project environment. Configure the tunnel's MCP command to start the same stdio entrypoint used by local Hosts:

   ```text
   uv run --no-sync --project <absolute-checkout-path> python -m cognivault.transports.mcp_stdio --config <absolute-config.chatgpt-readonly.local.toml-path>
   ```

   Pass this command using the official `tunnel-client init --sample sample_mcp_stdio_local ... --mcp-command ...` flow. On Windows, follow `tunnel-client help quickstart` for its command-line quoting and environment setup. Keep the profile outside the Git checkout and do not put credentials in it.

3. Set the runtime key as `CONTROL_PLANE_API_KEY`, then use the official client to initialize the named profile, run `tunnel-client doctor --profile <profile> --explain`, and start `tunnel-client run --profile <profile>`. Keep that process running while ChatGPT discovers or calls the app.

4. In the target ChatGPT workspace, enable Developer Mode if required, create an app, select **Tunnel** as the connection, and select the associated `tunnel_id`. The Platform tunnel must be associated with the target ChatGPT workspace. Tunnel Read + Use and ChatGPT Developer Mode are separate permissions. Feature availability depends on the account plan; consult the current Help Center article.

## Validation status

CogniVault's stdio transport and synthetic MCP subprocess tests are covered by the repository test suite. This repository does not provision an OpenAI tunnel, runtime key, Platform organization, or ChatGPT workspace app. A live tunnel discovery and tool call therefore remains unverified until an owner configures those account-side items. Do not use personal Study or History data as a setup probe; first test the tunnel with a separate synthetic read-only Gateway config and synthetic records.

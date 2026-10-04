# P13 offline inventory

此工具只处理显式旧输入；不连接 Gateway，不操作 V2 target，不执行 normalization。
V2 获取/导入/验收仍走正式 MCP/Gateway。准备一个 Git 外 JSON descriptor manifest，
其中所有 root 与 protected_paths 都使用实际绝对路径，仅在私有文件中保存。

```json
{
  "schema_version": 1,
  "protected_paths": ["<ABSOLUTE_V2_TARGET_ROOT>"],
  "sources": [
    {
      "scope": "codex_current",
      "source_type": "codex",
      "root": "<ABSOLUTE_LEGACY_INPUT_ROOT>",
      "suffixes": [".jsonl"],
      "acquisition_method": "bounded_local_inventory",
      "evidence_kind": "raw_session"
    },
    {
      "scope": "chatgpt_export",
      "source_type": "chatgpt",
      "state": "waiting_for_export"
    }
  ]
}
```

替换以上占位符，并保护所有 V2 Study/History/Assets/runtime 根。未定位的来源不能
随便猜路径；使用 waiting_for_export/not_found/review_required/gateway_unavailable。

从当前开发源码执行时，先刷新非 editable 安装。首次从旧 distribution 升级，应按 [README](../README.md#upgrade-from-the-former-distribution) 使用新环境或先卸载 `chatgpt-study-system-v2` 再安装 `cognivault`，避免共享 shim / CLI 被旧包卸载删除。只运行 `--no-sync` 会复用旧包，旧版本可能不含本模块。P13 当时的本地 wheel 已独立安装并做两次合成 inventory smoke；该历史结果不代表改名后的真实 Host 验收。

```powershell
uv sync --extra dev --no-editable --reinstall-package cognivault
uv run --no-sync python -m cognivault.migration.history_acquire --manifest '<PRIVATE_MANIFEST>' --ledger '<PRIVATE_LEDGER>'
```

manifest 与 ledger 都不能位于 Git checkout；ledger 不能与输入或保护目标重叠。
退出码 0 表示本轮 inventory 无获取错误，1 表示明确输入获取失败，2 表示无效配置/账本。
0 不表示来源完整、已导入或 P13 Gate PASS。

输出只有固定类别、计数及状态，明确 `INVENTORY_ONLY`/`unverified`。
record_estimate 是源记录估计；message_estimate 只计明确 messages 数组，仍非 canonical。
structural_probe 不是平台完整 importer，unknown/partial/ambiguous 不生成规范化记录。
same-category/same-byte copies 可在 ledger 去重，获取位置与不同文件版本仍保留。

错误记录和解决事件保留完整历史；public summary 将当前失败与历史失败分开。
重跑不会将 imported/reused 降回 parsed，也不会把 duplicate discovery 当成 import。
v1 ledger 的完整 schema 验证后可以事务升级到 v2；未知/不兼容 schema 被拒绝。
snapshot 应保持不可变，恢复验证必须使用独立副本，不能直接打开 snapshot 做 schema upgrade。

新 `imported` / `reused` outcome evidence 使用 `authority="cognivault"`。历史已存 `authority="study_system"` 作为弃用 provenance 继续可读且不改写；schema、hash、source identity 保持原约定。ledger 的读取结果不证明当前 Gateway 中的数据状态。

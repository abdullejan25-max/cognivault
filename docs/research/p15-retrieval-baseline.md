# P15 retrieval baseline

2026-10-07，Task 3 交付独立 synthetic gold、评价器与运行入口。**质量 baseline 尚未执行**；Task 4 必须在独立审阅后冻结当前代码运行。此页不把单测和 QMD sentinel smoke 当作检索质量结论，production / native Host 仍为 UNKNOWN。

Gold revision `p15-synthetic-1`，先行提交 `64a5f19acf0d4fcb3dfbcfa016ec0e6ac0def4a4`。32 个 case、四域各 8 个；Study 16 文件、Documents 12 本虚构资料 / 141 页、Wrong Answer 16 sources、History 16 canonical conversations + 1 source-only metadata。每域至少 10 个候选/干扰对象。所有文字、题目、聊天和同图不同题均为人工发明，不取自真实材料。

规范 JSON 摘要（manifest + 按固定域顺序的 cases，sorted keys / UTF-8 / compact JSON）：

`07ebc25e54c2003c24913fa5dbe8603d1a8b85a3fb681c7e65be6d7b82a4e8ad`

运行入口：

```text
python scripts/evaluate_retrieval.py --validate
python scripts/evaluate_retrieval.py --run --qmd-node <explicit-node.exe> --qmd-cli <explicit-qmd.js>
```

使用当前 checkout 的 `src`；不要以旧 installed wheel 代替。CLI 自建短的 `p15-*` 临时根，所有数据、inbox、receipts、QMD config/cache/index/home 都在该根。`--run` 输出该根中的 `result.json` 路径；结果应留在 Git 外。必须同时指定 Node 与 QMD JS；未提供真实 QMD 时 Study 明列 `unsupported`，不使用 fake backend 计分。QMD adapter 检查 package `@tobilu/qmd` 2.8.3，报告记录 Node 版本和 CLI SHA-256。

Gold IDs 从 ingestion 返回值、确定性 native chunk identity、History `source_system + native_key` 绑定；初始化禁止搜索。History manifest 经 Gateway ingest 后，Gateway verify / fetch 逐一核实 source、canonical view / message 和人工文本。Documents 每页一 chunk；capacity 使用另一个 profile，三份内容互异的虚构资料提供 2,049 chunks，原 default profile 不受污染。该 probe 的 expected `PAYLOAD_TOO_LARGE` 只计合同结果，同时明列检索容量缺口，不算正确空结果。

评价单位分别为 file、chunk/page、wrong-answer source、canonical conversation。History source-only metadata 独立 group，legacy item path 明列未测，二者不混入 canonical 或四域质量宏平均。

输出包含每 case 原始 native results、alias ranking、独立 readbacks、query elapsed、readback elapsed、errors、unsupported、eligibility、relevant counts；每域 Recall@1/3/5/10、MRR@10、source/page/citation case correctness 与 returned-item 原始分母、returned/gold locator coverage、no-result precision、negative empty recall、false-positive return rate、filter consistency、cold/warm samples 和 median/p95。重复 ID 在 top-k 内只计一次；MRR 保持原始排名。漏检/otherwise-supported errors 为零分；错误不算 empty。null locator 计 N/A，未知额外结果不冒领准确率。Citation 只核实检索 evidence，并不评价 Agent 最终答案；Wrong Answer bundle 可读回完整版本，但 citation evidence 只采用最新 analysis。现有 `source_system` scope 另作 unscoped control，记录应排除项与 relevant retention；unsupported scopes 不做 client filtering。

Cold 是每 case 一个 fresh Python worker 的第一条 Gateway query，索引已建好；计时包含 Gateway query 和 QMD subprocess，不包含 fixture setup，不声称清空 OS cache。随后至少五次同参数 warm query；全部样本与错误保留，报告 cold/warm ranking 是否一致。

2026-10-07 定向验证：`tests/test_retrieval_evaluation.py` + `tests/test_retrieval_runner.py`，显式提供真实 QMD Node / CLI 的一次最终运行 **23 passed / 22.78s**。先 RED 后 GREEN，独立错误 ranking 覆盖错书/页、重复、漏检、假空、false positive、越 scope、unsupported、error 与 N/A；runner 覆盖无搜索绑定、Gateway ingest、真实 isolated QMD sentinel、capacity guard、timeout / malformed worker output、manifest roundtrip 与 unsupported diagnostic status 保全。未运行完整 suite 或 32-case quality baseline。

人工错误样例验证：rank `[b65, a65, a65]`，gold `{a65,a66}`，Recall@1=0/2，Recall@3=1/2，MRR@10=1/2，returned source correctness=1/2，source/page/citation case correctness均0/1。positive 假空 + negative 真空产生 no-result precision=1/2；另一个 negative timeout 保留 negative empty recall=1/2，不被记为空。

Task 4 填写实际 baseline 的 commit、版本/profile manifest、32 case 分母、unsupported/error/contract coverage、定位缺口与 latency，并给出 raw result.json 的 Git 外证据位置。当前无依据调整 backend，也无依据宣称生产检索质量。

Task 3b 修正 Windows worker 的输出协议：child 显式 `PYTHONIOENCODING=utf-8` / `PYTHONUTF8=1`，覆盖继承的 cp936；缺失 stdout 或非 object JSON 记 `WORKER_BAD_OUTPUT`。Search 成功但 evidence readback 失败时，保留所有 native ranking / result 与已成功的 readbacks，`elapsed_ms` 保持 query-only，失败 readback 耗时另记；case 仍显式 error、质量零分。新增真实中文 subprocess 回归先复现 readerthread UTF-8 decode failure / stdout None，修复后 P15 定向验证 **28 passed / 21.91s**（含真实 QMD synthetic sentinel）。Gold hash、算法与指标分母未改；首次失败 baseline 没有有效结果，应在新 owned runtime 重新运行。

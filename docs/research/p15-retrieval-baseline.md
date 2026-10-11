# P15 retrieval baseline

2026-10-07，已在独立审阅后的固定实现上执行 32-case baseline。证据等级 **SYNTHETIC ONLY**；真实 production / native MCP Host **UNKNOWN**。四域 Recall@10 宏平均 0.95 只覆盖 capability-supported 正例，不能代表全部 32 cases 或生产质量。

Baseline code commit `a663156bc5449355bbc87423a2aa2a6ca44ae793`；生成时间 `2026-10-07T09:08:42.500169+00:00`。新增一致性测试与本报告的交付 commit 不改变本次 baseline 的代码标识。此前 `5b29469` 失败于 Windows worker 输出编码，没有有效质量结果；本次使用含 `e8b2ee9` 修正的已审阅实现。

Gold revision `p15-synthetic-1`，先行 authored gold commit `64a5f19acf0d4fcb3dfbcfa016ec0e6ac0def4a4`，规范 fixture SHA-256 `07ebc25e54c2003c24913fa5dbe8603d1a8b85a3fb681c7e65be6d7b82a4e8ad`。原完整 result.json 保留于外部 owned temporary runtime；原件 SHA-256 `a26d711596f63658858c057d668fcd711fad1c8b6432167e86ef35b161de963d`，字节数 1516508。公开文档省略私人绝对路径，内部 task-4-report 记录实际证据位置。下列 raw summary 与 case 投影均直接来自原件。

## 可重复条件

在上述固定 code commit 使用当前 `src`，先校验再运行；显式 Node / QMD JS 不可换为 fake backend。运行根由 runner 新建，所有 synthetic stores/inbox/receipts 与 QMD index/config/cache/home 都在该根。原始材料保留，不调用 embed，不下载模型。

```text
python scripts/evaluate_retrieval.py --validate
python scripts/evaluate_retrieval.py --run --qmd-node <explicit-node.exe> --qmd-cli <explicit-qmd.js>
```

Python 3.12.8；mcp 1.30.0；pypdf 6.19.0；Node v24.19.0；@tobilu/qmd 2.8.3；CLI SHA-256 `0dd4fd702adb83f27a69e59725478524ee48d00580ab6e9848dcde933000d99a`。limit=10，seed=15，固定 order=Study01..08 → Documents01..08 → WrongAnswer01..08 → History01..08；profiles default / capacity-2049。代码文件哈希：

```json
{
  "scripts/evaluate_retrieval.py": "1f72489c20466b0936578354086884fb836d052d62b5e03e0f566bc312ca0492",
  "scripts/retrieval_runner.py": "4008e281ce7d67a1e3e049cda87e7fcd924106706f93bcb40f390bb53e581e2e",
  "scripts/retrieval_metrics.py": "d5964c3f914330a89b6e8a6621babf017a6b7859ff407df08a9c7817920c14e3"
}
```

每域 8 cases；候选 Study 16 invented files、Documents 12 invented books / 141 pages、Wrong Answer 16 sources、History 16 canonical conversations + 1 source-only metadata。每域至少 10 个候选/干扰对象。所有文字与同图不同题均人工发明。Gold ID 由 ingestion/native identity 绑定，准备阶段禁止搜索，Gateway source verify/fetch 校验 canonical16/source-only1。Document 每页一 chunk；独立 capacity profile 三份互异资料共 2049 chunks，不污染 default。

## 质量与 coverage

32 cases：23 quality-supported（含 source-only1；四域主统计22），unsupported8，contract1；error1 是 contract case documents-05 的预期 PAYLOAD_TOO_LARGE，contract PASS1/1。否则支持路径无 error、无 timeout。unsupported 仍保留原生 unscoped diagnostic ranking，既未 client filtering，也未计入 supported 质量。source-only 单列且不进入四域宏平均；legacy History item path 未测。

Recall 分子为 case Recall 之和，分母为 eligible 正例数；不是跨域混合 hit 数。MRR 分子为首个 relevant 原始排名倒数之和。下文 case 表的 R1/3/5/10 则是 matched gold 数 / 本 case gold 数，可以核验这些均值。negative / contract / unsupported 不冒充满分。宏平均对四个主路径等权。

| path | cases / quality | R@1 | R@3 | R@5 | R@10 | MRR@10 |
| --- | --- | --- | --- | --- | --- | --- |
| study/study | 8/6 | 3.5/5 = 0.700000 | 4/5 = 0.800000 | 4/5 = 0.800000 | 4/5 = 0.800000 | 4/5 = 0.800000 |
| documents/documents | 8/4 | 2.5/3 = 0.833333 | 3/3 = 1.000000 | 3/3 = 1.000000 | 3/3 = 1.000000 | 3/3 = 1.000000 |
| wrong_answer/wrong_answer | 8/7 | 3/6 = 0.500000 | 6/6 = 1.000000 | 6/6 = 1.000000 | 6/6 = 1.000000 | 5/6 = 0.833333 |
| history/canonical | 7/5 | 4/4 = 1.000000 | 4/4 = 1.000000 | 4/4 = 1.000000 | 4/4 = 1.000000 | 4/4 = 1.000000 |
| history/source_only | 1/1 | 1/1 = 1.000000 | 1/1 = 1.000000 | 1/1 = 1.000000 | 1/1 = 1.000000 | 1/1 = 1.000000 |
| 4-domain macro | 4 paths | 3.03333/4 = 0.758333 | 3.8/4 = 0.950000 | 3.8/4 = 0.950000 | 3.8/4 = 0.950000 | 3.63333/4 = 0.908333 |

source/page/citation case correctness 要求返回集合完全等于 gold 且每条证据正确；额外非 gold 返回会失败。returned-item correctness 另列，每域单位保持 file / chunk-page / source / canonical conversation。null locator=N/A；未知额外结果为 unknown 而不获得准确率信用。Wrong Answer citation 仅使用最新 analysis；这里核实 retrieval evidence，未评估 Agent 最终答案。

| path | source cases | page cases | citation cases | filter cases | source items | page items | citation items |
| --- | --- | --- | --- | --- | --- | --- | --- |
| study/study | 4/5 = 0.800000 | 0/0 (N/A) | 4/5 = 0.800000 | 0/0 (N/A) | 5/5 = 1.000000 | 0/0 (N/A) | 5/5 = 1.000000 |
| documents/documents | 3/3 = 1.000000 | 3/3 = 1.000000 | 3/3 = 1.000000 | 0/0 (N/A) | 4/4 = 1.000000 | 4/4 = 1.000000 | 4/4 = 1.000000 |
| wrong_answer/wrong_answer | 4/6 = 0.666667 | 0/0 (N/A) | 4/6 = 0.666667 | 0/0 (N/A) | 8/10 = 0.800000 | 0/2 = 0.000000 | 8/10 = 0.800000 |
| history/canonical | 3/4 = 0.750000 | 0/0 (N/A) | 3/4 = 0.750000 | 1/1 = 1.000000 | 4/5 = 0.800000 | 0/1 = 0.000000 | 4/5 = 0.800000 |
| history/source_only | 1/1 = 1.000000 | 0/0 (N/A) | 1/1 = 1.000000 | 1/1 = 1.000000 | 1/1 = 1.000000 | 0/0 (N/A) | 1/1 = 1.000000 |

无结果指标的分母单列；Study positive false-empty 导致 precision=1/2，而不是所有空结果皆正确。

| path | no-result precision | negative empty recall | false-positive return rate |
| --- | --- | --- | --- |
| study/study | 1/2 = 0.500000 | 1/1 = 1.000000 | 0/1 = 0.000000 |
| documents/documents | 1/1 = 1.000000 | 1/1 = 1.000000 | 0/1 = 0.000000 |
| wrong_answer/wrong_answer | 1/1 = 1.000000 | 1/1 = 1.000000 | 0/1 = 0.000000 |
| history/canonical | 1/1 = 1.000000 | 1/1 = 1.000000 | 0/1 = 0.000000 |
| history/source_only | 0/0 (N/A) | 0/0 (N/A) | 0/0 (N/A) |

唯一 canonical 原生 scope pair 为 history-03：unscoped control 中排除1个越 source_system 的命中，保留 gold1，PASS；source-only history-06 的 source_system control 排除0，保留gold1，PASS。其余无 scope pair 实测，不推断支持。

Locator 表格式 `present/applicable; N/A; unknown`。gold coverage 以所有 relevant evidence 为分母，可暴露漏检；returned coverage 只说明已返回项的定位字段覆盖。supported 汇总中 chapter 全部 N/A，不能声称 chapter 定位已实现。

| path | coverage | source | page | chapter |
| --- | --- | --- | --- | --- |
| study/study | gold | 5/6; 0; 0 | 0/0; 6; 0 | 0/0; 6; 0 |
| study/study | locator_coverage | 5/5; 0; 0 | 0/0; 5; 0 | 0/0; 5; 0 |
| documents/documents | gold | 4/4; 0; 0 | 4/4; 0; 0 | 0/0; 4; 0 |
| documents/documents | locator_coverage | 4/4; 0; 0 | 4/4; 0; 0 | 0/0; 4; 0 |
| wrong_answer/wrong_answer | gold | 8/8; 0; 0 | 0/0; 8; 0 | 0/0; 8; 0 |
| wrong_answer/wrong_answer | locator_coverage | 8/8; 0; 2 | 0/0; 8; 2 | 0/0; 8; 2 |
| history/canonical | gold | 4/4; 0; 0 | 0/0; 4; 0 | 0/0; 4; 0 |
| history/canonical | locator_coverage | 4/4; 0; 1 | 0/0; 4; 1 | 0/0; 4; 1 |
| history/source_only | gold | 1/1; 0; 0 | 0/0; 1; 0 | 0/0; 1; 0 |
| history/source_only | locator_coverage | 1/1; 0; 0 | 0/0; 1; 0 | 0/0; 1; 0 |

## 每个 case 的证据与缺口

`matched` 仅列返回与 relevant gold 的交集，`extra` 为非 gold 返回；`R1/3/5/10` 保留原始 matched/gold 计数，即使 unsupported 也只是诊断而非质量信用。S/P/C 为 source/page/citation case score，`-` 为不适用或不计质量。G定位依次 source/page/chapter，格式 present/applicable（N/A）。全部32 case的完整 gold、返回顺序、各 cutoff 与 locator计数在下表；原始 native results/readbacks 在外部原件。

| case | status | gold | ordered native aliases | matched | extra | R1/3/5/10 counts | S/P/C | G定位 S/P/Ch | gap |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| study-01 | ok | study:fractions.md | study:fractions.md | study:fractions.md | ∅ | 1/1/1/1 of 1 | 1/-/1 | 1/1(0); 0/0(1); 0/0(1) | none measured |
| study-02 | ok | study:equal-parts.md | ∅ | ∅ | ∅ | 0/0/0/0 of 1 | 0/-/0 | 0/1(0); 0/0(1); 0/0(1) | algorithm lexical/semantic miss: positive empty; missing study:equal-parts.md |
| study-03 | ok | study:fractions.md, study:equal-parts.md | study:fractions.md, study:equal-parts.md | study:fractions.md, study:equal-parts.md | ∅ | 1/2/2/2 of 2 | 1/-/1 | 2/2(0); 0/0(2); 0/0(2) | multi-gold: relevant at 1,2; R1 ceiling=1/2 |
| study-04 | ok | study:inertia.md | study:inertia.md | study:inertia.md | ∅ | 1/1/1/1 of 1 | 1/-/1 | 1/1(0); 0/0(1); 0/0(1) | none measured |
| study-05 | ok | study:density.md | study:density.md | study:density.md | ∅ | 1/1/1/1 of 1 | 1/-/1 | 1/1(0); 0/0(1); 0/0(1) | none measured |
| study-06 | unsupported | study:fractions.md | study:equal-parts.md, study:fractions.md | study:fractions.md | study:equal-parts.md | 0/1/1/1 of 1 | -/-/- | 1/1(0); 0/0(1); 0/0(1) | unsupported API: page_range |
| study-07 | unsupported | study:fractions.md | study:equal-parts.md, study:fractions.md | study:fractions.md | study:equal-parts.md | 0/1/1/1 of 1 | -/-/- | 1/1(0); 0/0(1); 0/0(1) | unsupported API: chapter_ids |
| study-08 | ok | ∅ | ∅ | ∅ | ∅ | 0/0/0/0 of 0 | -/-/- | 0/0(0); 0/0(0); 0/0(0) | negative true empty |
| documents-01 | unsupported | book-a:p65:c1 | book-a:p65:c1, book-b:p65:c1 | book-a:p65:c1 | book-b:p65:c1 | 1/1/1/1 of 1 | -/-/- | 1/1(0); 1/1(0); 0/0(1) | unsupported API: source_ids |
| documents-02 | unsupported | book-a:p64:c1, book-a:p65:c1 | book-a:p64:c1, book-a:p65:c1, book-a:p66:c1 | book-a:p64:c1, book-a:p65:c1 | book-a:p66:c1 | 1/2/2/2 of 2 | -/-/- | 2/2(0); 2/2(0); 0/0(2) | unsupported API: page_range |
| documents-03 | ok | book-a:p64:c1, book-a:p65:c1 | book-a:p64:c1, book-a:p65:c1 | book-a:p64:c1, book-a:p65:c1 | ∅ | 1/2/2/2 of 2 | 1/1/1 | 2/2(0); 2/2(0); 0/0(2) | multi-gold: relevant at 1,2; R1 ceiling=1/2 |
| documents-04 | ok | book-a:p66:c1 | book-a:p66:c1 | book-a:p66:c1 | ∅ | 1/1/1/1 of 1 | 1/1/1 | 1/1(0); 1/1(0); 0/0(1) | none measured |
| documents-05 | error | ∅ | ∅ | ∅ | ∅ | 0/0/0/0 of 0 | -/-/- | 0/0(0); 0/0(0); 0/0(0) | capacity: expected PAYLOAD_TOO_LARGE; contract PASS |
| documents-06 | unsupported | book-b:p64:c1 | book-lantern-festival:p1:c1, book-b:p64:c1, book-b:p65:c1 | book-b:p64:c1 | book-lantern-festival:p1:c1, book-b:p65:c1 | 0/1/1/1 of 1 | -/-/- | 1/1(0); 1/1(0); 0/0(1) | unsupported API: page_range,source_ids |
| documents-07 | ok | book-a:p65:c1 | book-a:p65:c1 | book-a:p65:c1 | ∅ | 1/1/1/1 of 1 | 1/1/1 | 1/1(0); 1/1(0); 0/0(1) | none measured |
| documents-08 | ok | ∅ | ∅ | ∅ | ∅ | 0/0/0/0 of 0 | -/-/- | 0/0(0); 0/0(0); 0/0(0) | negative true empty |
| wrong_answer-01 | ok | wrong:fractions | wrong:equal-parts, wrong:fractions | wrong:fractions | wrong:equal-parts | 0/1/1/1 of 1 | 0/-/0 | 1/1(0); 0/0(1); 0/0(1) | extra equal-parts; relevant at 2 |
| wrong_answer-02 | ok | wrong:fractions, wrong:equal-parts | wrong:equal-parts, wrong:fractions | wrong:equal-parts, wrong:fractions | ∅ | 1/2/2/2 of 2 | 1/-/1 | 2/2(0); 0/0(2); 0/0(2) | multi-gold: relevant at 1,2; R1 ceiling=1/2 |
| wrong_answer-03 | ok | wrong:fractions, wrong:equal-parts | wrong:equal-parts, wrong:fractions | wrong:equal-parts, wrong:fractions | ∅ | 1/2/2/2 of 2 | 1/-/1 | 2/2(0); 0/0(2); 0/0(2) | multi-gold: relevant at 1,2; R1 ceiling=1/2 |
| wrong_answer-04 | ok | wrong:equal-parts | wrong:equal-parts | wrong:equal-parts | ∅ | 1/1/1/1 of 1 | 1/-/1 | 1/1(0); 0/0(1); 0/0(1) | none measured |
| wrong_answer-05 | ok | wrong:fractions | wrong:fractions | wrong:fractions | ∅ | 1/1/1/1 of 1 | 1/-/1 | 1/1(0); 0/0(1); 0/0(1) | none measured |
| wrong_answer-06 | unsupported | wrong:fractions, wrong:equal-parts | wrong:equal-parts, wrong:fractions | wrong:equal-parts, wrong:fractions | ∅ | 1/2/2/2 of 2 | -/-/- | 2/2(0); 0/0(2); 0/0(2) | unsupported API: time_range |
| wrong_answer-07 | ok | wrong:fractions | wrong:equal-parts, wrong:fractions | wrong:fractions | wrong:equal-parts | 0/1/1/1 of 1 | 0/-/0 | 1/1(0); 0/0(1); 0/0(1) | extra equal-parts; relevant at 2 |
| wrong_answer-08 | ok | ∅ | ∅ | ∅ | ∅ | 0/0/0/0 of 0 | -/-/- | 0/0(0); 0/0(0); 0/0(0) | negative true empty |
| history-01 | ok | conversation:lantern | conversation:lantern, conversation:lantern-festival | conversation:lantern | conversation:lantern-festival | 1/1/1/1 of 1 | 0/-/0 | 1/1(0); 0/0(1); 0/0(1) | extra lantern-festival |
| history-02 | ok | conversation:orbit | conversation:orbit | conversation:orbit | ∅ | 1/1/1/1 of 1 | 1/-/1 | 1/1(0); 0/0(1); 0/0(1) | none measured |
| history-03 | ok | conversation:inertia | conversation:inertia | conversation:inertia | ∅ | 1/1/1/1 of 1 | 1/-/1 | 1/1(0); 0/0(1); 0/0(1) | none measured |
| history-04 | unsupported | conversation:lantern | conversation:lantern, conversation:lantern-festival | conversation:lantern | conversation:lantern-festival | 1/1/1/1 of 1 | -/-/- | 1/1(0); 0/0(1); 0/0(1) | unsupported API: time_range |
| history-05 | unsupported | conversation:fractions, conversation:equal-parts | conversation:equal-parts, conversation:fractions | conversation:equal-parts, conversation:fractions | ∅ | 1/2/2/2 of 2 | -/-/- | 2/2(0); 0/0(2); 0/0(2) | unsupported API: source_ids |
| history-06 | ok | source-only:metadata | source-only:metadata | source-only:metadata | ∅ | 1/1/1/1 of 1 | 1/-/1 | 1/1(0); 0/0(1); 0/0(1) | none measured |
| history-07 | ok | conversation:equal-parts | conversation:equal-parts | conversation:equal-parts | ∅ | 1/1/1/1 of 1 | 1/-/1 | 1/1(0); 0/0(1); 0/0(1) | none measured |
| history-08 | ok | ∅ | ∅ | ∅ | ∅ | 0/0/0/0 of 0 | -/-/- | 0/0(0); 0/0(0); 0/0(0) | negative true empty |

## 延迟与一致性

cold 为索引建好后 fresh Python process 的首个 Gateway query；warm 为同一 worker 的5次同参数重复。包含 QMD Node subprocess 与 Gateway query，排除 fixture setup / evidence readback；没有 OS cache flush。每 case 5 warm，共32 cold +160 warm；capacity 的6次预期错误耗时及unsupported诊断耗时均保留，不能当成功查询 latency。P95 为 nearest-rank；非完整端到端 Agent/Host latency。

| path | cold n / median / P95 ms | warm n / median / P95 ms |
| --- | --- | --- |
| study/study | 8 / 345.099 / 391.141 | 40 / 337.665 / 399.053 |
| documents/documents | 8 / 32.723 / 48.259 | 40 / 31.322 / 42.301 |
| wrong_answer/wrong_answer | 8 / 49.566 / 55.682 | 40 / 45.967 / 48.724 |
| history/canonical | 7 / 13.173 / 15.194 | 35 / 5.541 / 8.554 |
| history/source_only | 1 / 12.634 / 12.634 | 5 / 5.911 / 7.183 |

31/31 successful-query cases（含8 unsupported 的 unscoped diagnostics）cold/warm alias ranking 相同，warm status 全为ok；capacity case 的6次error空诊断ranking也相同，另列而不称检索成功。无timeout。下方 raw summary 保存全部原始 latency samples，不只给汇总数。

新增真实 QMD acceptance test 在另一个 owned synthetic runtime：新增一个 invented sentinel file→update得到1 hit；修改该文本并新增第二文件→incremental update得到2 hits；通过另一个全新 QmdRuntime重建派生index，得到同样2个 source_path；fresh Python worker first query与5次warm排序相同。原始 authored files保留，不删除候选，不接触真实index，不调用embed。检查既有行为而未改backend，无需伪造实现RED。初次测试使用错误返回字段 source_uri 而出现KeyError，是测试自身错误；按真实Gateway合同source_path纠正后通过。最终focused：29 passed in38.96s，包含此真实QMD检查与原有sentinel；未跑full suite。

## 测得 gap 与 P17 决策

建议 **P17 单一候选：Documents native source_ids + page_range filters**。documents-01要求book-a/page65却诊断返回book-b/page65；documents-02要求64..65却包含page66。两者gold均独立，source/page locator已经具备，问题属于缺API scope，不证明需要新索引。先通过Gateway/native contract实现并用固定gold测量filter与gold retention；不能用client filtering伪装支持。documents-06同时缺source_ids/page_range；study-07的chapter_ids缺稳定chapter合同，保持unsupported，不纳入此候选。

其他缺口保留：Study study-02为自然语言/词法匹配漏检；study-03、documents-03与wrong_answer-02/03在前2位找齐两个gold，Recall@1=1/2是分母固有限制而非排名缺陷；wrong_answer-01/07、history-01为额外非gold命中；Study page/chapter、WrongAnswer time、History time/source_ids scopes为unsupported；documents-05为2049候选capacity边界。当前没有证据引入vector/newbackend/schema rewrite或以索引优化消除scope缺口。已知fixture/evaluator错误本次未出现；新增测试字段错误独立纠正。真实资料分布、权限、容量与生产效果均UNKNOWN，不能从synthetic指标外推。

## 原 summary（精确 JSON，含 raw samples）

下列对象从原件原样序列化（允许空白差异），没有重算评分或变更分母，可结合case表复核。源gold仍为冻结fixture，外部原件SHA见开头。

<details>
<summary>展开 machine summary</summary>

```json
{
  "groups": {
    "study/study": {
      "cases": 8,
      "quality_supported": 6,
      "unsupported_cases": [
        "study-06",
        "study-07"
      ],
      "error_cases": [],
      "no_result_precision": {
        "numerator": 1,
        "denominator": 2,
        "value": 0.5
      },
      "negative_empty_recall": {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0
      },
      "false_positive_return_rate": {
        "numerator": 0,
        "denominator": 1,
        "value": 0.0
      },
      "recall@1": {
        "numerator": 3.5,
        "denominator": 5,
        "value": 0.7
      },
      "recall@3": {
        "numerator": 4.0,
        "denominator": 5,
        "value": 0.8
      },
      "recall@5": {
        "numerator": 4.0,
        "denominator": 5,
        "value": 0.8
      },
      "recall@10": {
        "numerator": 4.0,
        "denominator": 5,
        "value": 0.8
      },
      "mrr@10": {
        "numerator": 4.0,
        "denominator": 5,
        "value": 0.8
      },
      "source_case": {
        "numerator": 4,
        "denominator": 5,
        "value": 0.8
      },
      "page_case": {
        "numerator": 0,
        "denominator": 0,
        "value": null
      },
      "citation_case": {
        "numerator": 4,
        "denominator": 5,
        "value": 0.8
      },
      "filter_case": {
        "numerator": 0,
        "denominator": 0,
        "value": null
      },
      "latency": {
        "cold": {
          "count": 8,
          "samples_ms": [
            391.1406999995961,
            344.07570000075793,
            345.1619999996183,
            342.700799999875,
            345.03570000015316,
            340.3679999992164,
            345.6365999991249,
            356.0386000008293
          ],
          "median_ms": 345.0988499998857,
          "p95_ms": 391.1406999995961
        },
        "warm": {
          "count": 40,
          "samples_ms": [
            328.67879999867,
            342.87460000086867,
            329.9650000008114,
            340.73329999955604,
            338.91849999963597,
            343.9308000015444,
            331.1620000004041,
            328.0869000009261,
            458.24030000039784,
            450.1540000001114,
            331.1885000002803,
            355.3197999990516,
            335.02339999904507,
            369.3335999996634,
            331.2980000009702,
            333.3292999996047,
            336.04709999963234,
            325.88380000015604,
            339.4533999999112,
            329.78020000155084,
            335.53870000105235,
            337.9484999986744,
            337.3814999995375,
            327.8608999989956,
            336.3617000013619,
            338.9105999995081,
            399.05349999935424,
            349.47550000106276,
            356.10889999952633,
            352.82549999828916,
            340.1412999992317,
            339.6364000000176,
            333.1010999991122,
            341.46770000006654,
            337.17600000090897,
            327.77180000084627,
            326.7916000004334,
            339.19280000009167,
            355.48000000017055,
            332.87580000069283
          ],
          "median_ms": 337.66499999910593,
          "p95_ms": 399.05349999935424
        }
      },
      "items": {
        "source": {
          "numerator": 5,
          "denominator": 5,
          "value": 1.0
        },
        "page": {
          "numerator": 0,
          "denominator": 0,
          "value": null
        },
        "citation": {
          "numerator": 5,
          "denominator": 5,
          "value": 1.0
        }
      },
      "locator_coverage": {
        "source_id": {
          "applicable": 5,
          "present": 5,
          "na": 0,
          "unknown": 0
        },
        "page_number": {
          "applicable": 0,
          "present": 0,
          "na": 5,
          "unknown": 0
        },
        "chapter_id": {
          "applicable": 0,
          "present": 0,
          "na": 5,
          "unknown": 0
        }
      },
      "gold_locator_coverage": {
        "source_id": {
          "applicable": 6,
          "present": 5,
          "na": 0
        },
        "page_number": {
          "applicable": 0,
          "present": 0,
          "na": 6
        },
        "chapter_id": {
          "applicable": 0,
          "present": 0,
          "na": 6
        }
      }
    },
    "documents/documents": {
      "cases": 8,
      "quality_supported": 4,
      "unsupported_cases": [
        "documents-01",
        "documents-02",
        "documents-06"
      ],
      "error_cases": [
        "documents-05"
      ],
      "no_result_precision": {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0
      },
      "negative_empty_recall": {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0
      },
      "false_positive_return_rate": {
        "numerator": 0,
        "denominator": 1,
        "value": 0.0
      },
      "recall@1": {
        "numerator": 2.5,
        "denominator": 3,
        "value": 0.8333333333333334
      },
      "recall@3": {
        "numerator": 3.0,
        "denominator": 3,
        "value": 1.0
      },
      "recall@5": {
        "numerator": 3.0,
        "denominator": 3,
        "value": 1.0
      },
      "recall@10": {
        "numerator": 3.0,
        "denominator": 3,
        "value": 1.0
      },
      "mrr@10": {
        "numerator": 3.0,
        "denominator": 3,
        "value": 1.0
      },
      "source_case": {
        "numerator": 3,
        "denominator": 3,
        "value": 1.0
      },
      "page_case": {
        "numerator": 3,
        "denominator": 3,
        "value": 1.0
      },
      "citation_case": {
        "numerator": 3,
        "denominator": 3,
        "value": 1.0
      },
      "filter_case": {
        "numerator": 0,
        "denominator": 0,
        "value": null
      },
      "latency": {
        "cold": {
          "count": 8,
          "samples_ms": [
            32.838500001162174,
            32.60770000088087,
            34.203199998955824,
            33.25980000045092,
            48.25900000105321,
            32.31910000067728,
            31.159699999989243,
            30.966600001193
          ],
          "median_ms": 32.72310000102152,
          "p95_ms": 48.25900000105321
        },
        "warm": {
          "count": 40,
          "samples_ms": [
            29.62630000001809,
            30.167400000209454,
            31.49680000024091,
            28.85690000039176,
            29.50100000089151,
            28.418600000804872,
            31.3877999997203,
            30.043999999179505,
            28.93200000107754,
            32.538699999349774,
            35.64910000022792,
            29.321199999685632,
            31.78920000027574,
            33.83620000022347,
            35.73529999994207,
            29.334999999264255,
            29.8930000008113,
            31.78980000120646,
            34.92030000052182,
            28.73150000050373,
            42.52900000028603,
            41.95219999928668,
            41.20130000046629,
            46.530599998732214,
            42.300799999793526,
            31.5721000006306,
            30.239499999879627,
            31.883399999060202,
            33.23209999871324,
            31.256099999154685,
            28.164600000309292,
            31.00819999963278,
            33.85269999853335,
            28.628300000491436,
            32.92510000028415,
            30.314300000100047,
            30.597200000556768,
            29.726200000368408,
            32.049400000687456,
            30.81200000087847
          ],
          "median_ms": 31.321949999437493,
          "p95_ms": 42.300799999793526
        }
      },
      "items": {
        "source": {
          "numerator": 4,
          "denominator": 4,
          "value": 1.0
        },
        "page": {
          "numerator": 4,
          "denominator": 4,
          "value": 1.0
        },
        "citation": {
          "numerator": 4,
          "denominator": 4,
          "value": 1.0
        }
      },
      "locator_coverage": {
        "source_id": {
          "applicable": 4,
          "present": 4,
          "na": 0,
          "unknown": 0
        },
        "page_number": {
          "applicable": 4,
          "present": 4,
          "na": 0,
          "unknown": 0
        },
        "chapter_id": {
          "applicable": 0,
          "present": 0,
          "na": 4,
          "unknown": 0
        }
      },
      "gold_locator_coverage": {
        "source_id": {
          "applicable": 4,
          "present": 4,
          "na": 0
        },
        "page_number": {
          "applicable": 4,
          "present": 4,
          "na": 0
        },
        "chapter_id": {
          "applicable": 0,
          "present": 0,
          "na": 4
        }
      }
    },
    "wrong_answer/wrong_answer": {
      "cases": 8,
      "quality_supported": 7,
      "unsupported_cases": [
        "wrong_answer-06"
      ],
      "error_cases": [],
      "no_result_precision": {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0
      },
      "negative_empty_recall": {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0
      },
      "false_positive_return_rate": {
        "numerator": 0,
        "denominator": 1,
        "value": 0.0
      },
      "recall@1": {
        "numerator": 3.0,
        "denominator": 6,
        "value": 0.5
      },
      "recall@3": {
        "numerator": 6.0,
        "denominator": 6,
        "value": 1.0
      },
      "recall@5": {
        "numerator": 6.0,
        "denominator": 6,
        "value": 1.0
      },
      "recall@10": {
        "numerator": 6.0,
        "denominator": 6,
        "value": 1.0
      },
      "mrr@10": {
        "numerator": 5.0,
        "denominator": 6,
        "value": 0.8333333333333334
      },
      "source_case": {
        "numerator": 4,
        "denominator": 6,
        "value": 0.6666666666666666
      },
      "page_case": {
        "numerator": 0,
        "denominator": 0,
        "value": null
      },
      "citation_case": {
        "numerator": 4,
        "denominator": 6,
        "value": 0.6666666666666666
      },
      "filter_case": {
        "numerator": 0,
        "denominator": 0,
        "value": null
      },
      "latency": {
        "cold": {
          "count": 8,
          "samples_ms": [
            55.68159999893396,
            46.847199999319855,
            50.30359999909706,
            48.82799999904819,
            47.38250000082189,
            50.505000001066946,
            50.37520000041695,
            46.348900001248694
          ],
          "median_ms": 49.565799999072624,
          "p95_ms": 55.68159999893396
        },
        "warm": {
          "count": 40,
          "samples_ms": [
            45.12059999979101,
            46.273599999040016,
            46.38920000070357,
            48.09659999955329,
            47.449800000322284,
            47.48190000100294,
            44.967599998926744,
            45.88549999971292,
            45.385900000837864,
            46.36409999875468,
            49.84160000094562,
            45.46940000000177,
            46.94770000060089,
            44.64100000041071,
            45.98170000099344,
            45.054600001094514,
            69.66169999941485,
            44.81780000060098,
            47.998800000641495,
            44.3174000010913,
            44.74999999911233,
            45.951999998578685,
            45.60459999993327,
            48.02829999971436,
            47.047699999893666,
            45.81260000122711,
            43.612400000711204,
            48.52529999880062,
            46.94709999967017,
            43.94539999884728,
            48.7239000012778,
            43.55629999918165,
            45.58619999988878,
            47.95420000118611,
            47.40080000010494,
            46.56289999911678,
            44.822199999543955,
            44.229399998584995,
            44.70760000003793,
            46.1873000003834
          ],
          "median_ms": 45.966849999786064,
          "p95_ms": 48.7239000012778
        }
      },
      "items": {
        "source": {
          "numerator": 8,
          "denominator": 10,
          "value": 0.8
        },
        "page": {
          "numerator": 0,
          "denominator": 2,
          "value": 0.0
        },
        "citation": {
          "numerator": 8,
          "denominator": 10,
          "value": 0.8
        }
      },
      "locator_coverage": {
        "source_id": {
          "applicable": 8,
          "present": 8,
          "na": 0,
          "unknown": 2
        },
        "page_number": {
          "applicable": 0,
          "present": 0,
          "na": 8,
          "unknown": 2
        },
        "chapter_id": {
          "applicable": 0,
          "present": 0,
          "na": 8,
          "unknown": 2
        }
      },
      "gold_locator_coverage": {
        "source_id": {
          "applicable": 8,
          "present": 8,
          "na": 0
        },
        "page_number": {
          "applicable": 0,
          "present": 0,
          "na": 8
        },
        "chapter_id": {
          "applicable": 0,
          "present": 0,
          "na": 8
        }
      }
    },
    "history/canonical": {
      "cases": 7,
      "quality_supported": 5,
      "unsupported_cases": [
        "history-04",
        "history-05"
      ],
      "error_cases": [],
      "no_result_precision": {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0
      },
      "negative_empty_recall": {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0
      },
      "false_positive_return_rate": {
        "numerator": 0,
        "denominator": 1,
        "value": 0.0
      },
      "recall@1": {
        "numerator": 4.0,
        "denominator": 4,
        "value": 1.0
      },
      "recall@3": {
        "numerator": 4.0,
        "denominator": 4,
        "value": 1.0
      },
      "recall@5": {
        "numerator": 4.0,
        "denominator": 4,
        "value": 1.0
      },
      "recall@10": {
        "numerator": 4.0,
        "denominator": 4,
        "value": 1.0
      },
      "mrr@10": {
        "numerator": 4.0,
        "denominator": 4,
        "value": 1.0
      },
      "source_case": {
        "numerator": 3,
        "denominator": 4,
        "value": 0.75
      },
      "page_case": {
        "numerator": 0,
        "denominator": 0,
        "value": null
      },
      "citation_case": {
        "numerator": 3,
        "denominator": 4,
        "value": 0.75
      },
      "filter_case": {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0
      },
      "latency": {
        "cold": {
          "count": 7,
          "samples_ms": [
            13.172999999369495,
            12.761600000885664,
            15.193699999144883,
            12.07070000054955,
            12.39030000033381,
            14.953399999285466,
            14.078700000027311
          ],
          "median_ms": 13.172999999369495,
          "p95_ms": 15.193699999144883
        },
        "warm": {
          "count": 35,
          "samples_ms": [
            8.28919999912614,
            8.554099998946185,
            6.195100000695675,
            5.541300000913907,
            5.694000001312816,
            5.114600000524661,
            4.865099999733502,
            4.836899999645539,
            5.493299999216106,
            4.883200001131627,
            4.843400000027032,
            4.646399998819106,
            6.593500000235508,
            8.837500001391163,
            5.463900000904687,
            6.199299999934738,
            5.216600000494509,
            4.6779000003880356,
            5.994999999529682,
            5.992300000798423,
            4.935100001603132,
            4.698900000221329,
            5.368399999497342,
            6.9447000005311565,
            8.079799999904935,
            4.763700000694371,
            5.211099998632562,
            6.171400000312133,
            7.8125,
            6.049800000255345,
            6.437800000639982,
            4.9201999991055345,
            4.7350000004371395,
            6.0476999988168245,
            6.350000001475564
          ],
          "median_ms": 5.541300000913907,
          "p95_ms": 8.554099998946185
        }
      },
      "items": {
        "source": {
          "numerator": 4,
          "denominator": 5,
          "value": 0.8
        },
        "page": {
          "numerator": 0,
          "denominator": 1,
          "value": 0.0
        },
        "citation": {
          "numerator": 4,
          "denominator": 5,
          "value": 0.8
        }
      },
      "locator_coverage": {
        "source_id": {
          "applicable": 4,
          "present": 4,
          "na": 0,
          "unknown": 1
        },
        "page_number": {
          "applicable": 0,
          "present": 0,
          "na": 4,
          "unknown": 1
        },
        "chapter_id": {
          "applicable": 0,
          "present": 0,
          "na": 4,
          "unknown": 1
        }
      },
      "gold_locator_coverage": {
        "source_id": {
          "applicable": 4,
          "present": 4,
          "na": 0
        },
        "page_number": {
          "applicable": 0,
          "present": 0,
          "na": 4
        },
        "chapter_id": {
          "applicable": 0,
          "present": 0,
          "na": 4
        }
      }
    },
    "history/source_only": {
      "cases": 1,
      "quality_supported": 1,
      "unsupported_cases": [],
      "error_cases": [],
      "no_result_precision": {
        "numerator": 0,
        "denominator": 0,
        "value": null
      },
      "negative_empty_recall": {
        "numerator": 0,
        "denominator": 0,
        "value": null
      },
      "false_positive_return_rate": {
        "numerator": 0,
        "denominator": 0,
        "value": null
      },
      "recall@1": {
        "numerator": 1.0,
        "denominator": 1,
        "value": 1.0
      },
      "recall@3": {
        "numerator": 1.0,
        "denominator": 1,
        "value": 1.0
      },
      "recall@5": {
        "numerator": 1.0,
        "denominator": 1,
        "value": 1.0
      },
      "recall@10": {
        "numerator": 1.0,
        "denominator": 1,
        "value": 1.0
      },
      "mrr@10": {
        "numerator": 1.0,
        "denominator": 1,
        "value": 1.0
      },
      "source_case": {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0
      },
      "page_case": {
        "numerator": 0,
        "denominator": 0,
        "value": null
      },
      "citation_case": {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0
      },
      "filter_case": {
        "numerator": 1,
        "denominator": 1,
        "value": 1.0
      },
      "latency": {
        "cold": {
          "count": 1,
          "samples_ms": [
            12.633999998797663
          ],
          "median_ms": 12.633999998797663,
          "p95_ms": 12.633999998797663
        },
        "warm": {
          "count": 5,
          "samples_ms": [
            5.91079999867361,
            5.61800000104995,
            6.692799999655108,
            7.18339999912132,
            5.424900000434718
          ],
          "median_ms": 5.91079999867361,
          "p95_ms": 7.18339999912132
        }
      },
      "items": {
        "source": {
          "numerator": 1,
          "denominator": 1,
          "value": 1.0
        },
        "page": {
          "numerator": 0,
          "denominator": 0,
          "value": null
        },
        "citation": {
          "numerator": 1,
          "denominator": 1,
          "value": 1.0
        }
      },
      "locator_coverage": {
        "source_id": {
          "applicable": 1,
          "present": 1,
          "na": 0,
          "unknown": 0
        },
        "page_number": {
          "applicable": 0,
          "present": 0,
          "na": 1,
          "unknown": 0
        },
        "chapter_id": {
          "applicable": 0,
          "present": 0,
          "na": 1,
          "unknown": 0
        }
      },
      "gold_locator_coverage": {
        "source_id": {
          "applicable": 1,
          "present": 1,
          "na": 0
        },
        "page_number": {
          "applicable": 0,
          "present": 0,
          "na": 1
        },
        "chapter_id": {
          "applicable": 0,
          "present": 0,
          "na": 1
        }
      }
    }
  },
  "coverage": {
    "all": 32,
    "quality_supported": 23,
    "unsupported": 8,
    "errors": 1,
    "contract": 1,
    "contract_pass": 1
  },
  "macro": {
    "recall@1": {
      "numerator": 3.033333333333333,
      "denominator": 4,
      "value": 0.7583333333333333
    },
    "recall@3": {
      "numerator": 3.8,
      "denominator": 4,
      "value": 0.95
    },
    "recall@5": {
      "numerator": 3.8,
      "denominator": 4,
      "value": 0.95
    },
    "recall@10": {
      "numerator": 3.8,
      "denominator": 4,
      "value": 0.95
    },
    "mrr@10": {
      "numerator": 3.6333333333333333,
      "denominator": 4,
      "value": 0.9083333333333333
    }
  }
}
```

</details>

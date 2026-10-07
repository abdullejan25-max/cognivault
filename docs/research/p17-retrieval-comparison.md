# P17 Documents 原生范围过滤比较

2026-10-07，单一候选采用进入主线准备：既有 `search_documents` 增加 canonical `source_ids` 与 inclusive physical `page_range`（包含两端的物理页码）。证据等级 **SYNTHETIC ONLY**；production / native MCP Host **UNKNOWN**。收益是可表达教材与页范围，并排除实测越范围证据；共同集合的排序质量没有提升，也没有回退。独立 review 与最终完整 suite 由 root 执行。

## 冻结条件与证据

Baseline code `a663156bc5449355bbc87423a2aa2a6ca44ae793`；本 task 起点 `8f3b9f2637937e2a503722f227d986562c6d3ccf`。候选代码/tests/runner 在干净提交 `6d4b5b57ee495c77e46b1b496fb857f7e1a15c10` 冻结后，仅运行一次 `--run`。本报告与 P18 文字更新在后续文档提交，不改变测量代码。

Gold revision `p15-synthetic-1`，SHA-256 `07ebc25e54c2003c24913fa5dbe8603d1a8b85a3fb681c7e65be6d7b82a4e8ad`，32 gold cases / 原始 scoring / limit=10 / seed=15 / order / default 与 capacity-2049 profiles 全部未变。独立 authored ingestion aliases 仅在调用 Gateway source scope 时翻译成 canonical document URI；无 client filtering。

旧 baseline raw 与公开 P15 文档保留。原件 SHA-256 `a26d711596f63658858c057d668fcd711fad1c8b6432167e86ef35b161de963d`（1516508 bytes）。候选 raw 生成时间 `2026-10-07T14:19:09.831704+00:00`，SHA-256 `43f33565c8f34bfed45f02d86cacfcc7a2a65011a00abc39c264ad5e4e1c97b2`（1496978 bytes）；原件和逐字节副本均保存在 Git 外 author-owned runtime，私有路径在 ignored task-6-report。没有读取真实教材、数据库、私人 QMD 配置或个人目录。

```text
python scripts/evaluate_retrieval.py --validate
python scripts/evaluate_retrieval.py --run --qmd-node <explicit-node.exe> --qmd-cli <explicit-qmd.js>
```

Python 3.12.8，mcp 1.30.0，pypdf 6.19.0；真实 Node v24.19.0 / QMD 2.8.3，CLI SHA-256 `0dd4fd702adb83f27a69e59725478524ee48d00580ab6e9848dcde933000d99a`。QMD ready=true，setup error=null；只使用新建 synthetic root 与 invented content，不调用 embed / 模型下载。

候选文件 SHA-256（scorer 与 CLI 不变）：

```json
{
  "scripts/evaluate_retrieval.py": "1f72489c20466b0936578354086884fb836d052d62b5e03e0f566bc312ca0492",
  "scripts/retrieval_runner.py": "423ab5d3d4d809bd7d23cf829a60b2e59fad16d4e89fa7f484e8e146a5ffb14c",
  "scripts/retrieval_metrics.py": "d5964c3f914330a89b6e8a6621babf017a6b7859ff407df08a9c7817920c14e3",
  "src\\cognivault\\adapters\\documents.py": "5113ce253e46f151eb590ae7e08bf2bcda1727b0cbc54584fc01be39914dcae0",
  "src\\cognivault\\gateway.py": "8aff48407aec400c9c02013177a8895a7ee885e602cfba2c6d7b915c0196114e",
  "src\\cognivault\\transports\\mcp_stdio.py": "a88db1b0682c6d647651d0dfc8d54ce99c47198f090b0a266e25c4eea3dc1850"
}
```

## 合同与边界

`source_ids` 为 1..64 项非空 list，元素必须严格匹配 `document://sha256/<64 lowercase hex>`；重复 URI 接受并去重，64/65 边界按原 list 长度计。语法有效但未知 URI 返回空集合。`page_range` 为长度恰好2的 list，两项必须是严格 int（拒绝 bool/float），范围1..999，start<=end。组合条件取交集；空list、错误类型、非法 URI、逆序或1000页不静默扩展范围。MCP schema 禁止未知字段，不开放 chapter/time。

过滤在原生 adapter 内、匹配与分页之前完成；total/has_more 描述 scoped 全匹配集合。旧 query/limit/offset 调用、返回 DTO、身份/来源/权限/数据库 schema 均不改变。2049 行探针仍先检查全 catalog 的2048候选总量；即使 scope 只含一页、未知文档或无匹配的页范围，超量仍报 PAYLOAD_TOO_LARGE，不借过滤绕过容量保护。它仍是 bounded native scan，没有新 index、ranking、数据库或搜索引擎。

## 共同支持集合：23 cases，分母固定

共同集合恰为 baseline 的23 supported cases，其中22主路径cases与1 source-only。正例分母为 Study5、Documents3、WrongAnswer6、canonical History4；source-only1另列。四个 negative 不冒充正例满分。新增3 scope cases不混入共同分母。逐 case status / 原始 alias ranking / Recall@1/3/5/10 / MRR@10 / source/page/citation/filter / returned与gold locator counts 全相同（23/23，变化0）；其余6非新增case的 status/ranking也保持原样。

| common path | positive denominator | R@5 baseline → candidate | MRR@10 baseline → candidate |
| --- | --- | --- | --- |
| study/study | 5 | 4/5 → 4/5 | 4/5 → 4/5 |
| documents/documents | 3 | 3/3 → 3/3 | 3/3 → 3/3 |
| wrong_answer/wrong_answer | 6 | 6/6 → 6/6 | 5/6 → 5/6 |
| history/canonical | 4 | 4/4 → 4/4 | 4/4 → 4/4 |
| history/source_only（独立） | 1 | 1/1 → 1/1 | 1/1 → 1/1 |
| four-domain macro | 4 paths | 3.8/4=.95 → .95 | 3.633333333333333/4=.9083333333333333 → 同值 |

共同 Document 正例 Recall@5 / MRR@10 原本已满分，不要求+0.05，不宣称排名提升。共同 source/page/citation items 与 gold locator coverage不回退；baseline Study02漏检、WrongAnswer01/07额外命中与History01额外命中仍存在，本候选未优化这些问题。

## 新增范围 coverage：独立3 cases

| case / native scope | unscoped control 排除的实际 IDs | scoped 实际返回 / retained gold | 越范围 |
| --- | --- | --- | --- |
| documents-01 / book-a | book-b:p65:c1 | book-a:p65:c1；1/1 | 0/1 |
| documents-02 / physical64..65 | book-a:p66:c1 | book-a:p64:c1,book-a:p65:c1；2/2 | 0/2 |
| documents-06 / book-b AND physical64 | book-b:p65:c1,book-lantern-festival:p1:c1 | book-b:p64:c1；1/1 | 0/1 |

三组各运行真实 Gateway scoped query 与无过滤 control，scope_pair PASS3/3，排除4条越范围命中，返回中越范围0/4，保留 scoped gold4/4。新cases的 Recall@5 case-sum3/3、MRR@10 case-sum3/3、source/page/citation/filter correctness各3/3；source/page/citation returned items各4/4，gold source/page locator各4/4，chapter0适用（N/A4）。documents-02 R@1=1/2仍受两个gold的分母限制；新case这些分数表示新增scope coverage，不能当排名A/B gain。三case在旧baseline是unsupported，仅有unscoped诊断；旧诊断的越范围4/8不能赋予supported质量分数。

总coverage32：supported23→26，unsupported8→5，contract1→1（PASS1/1），expected error1→1，otherwise-supported errors0、timeouts0。仍unsupported：study-06 page_range、study-07 chapter_ids、wrong_answer-06 time_range、history-04 time_range、history-05 source_ids。新cases全正例，无negative分母；共同4主域negative empty recall各1/1、false-positive return rate各0/1，source-only0/0；Study no-result precision仍1/2，其余主域1/1。新增范围外false-positive明确为0/4返回项，与negative-query false-positive分开。

## 延迟与一致性

只比较共同 Documents03/04/07/08：每侧4 cold与20 warm（每case5次），排除新增scope与capacity/unsupported组；P95为nearest-rank。cold为prebuilt index后的fresh process首个Gateway查询，warm包括Gateway查询，不含evidence readback；没有OS cache flush。

| common Documents | baseline | candidate | change |
| --- | --- | --- | --- |
| cold median / P95 ms，n=4 | 32.210 / 34.203 | 32.469 / 35.373 | P95 +3.42% |
| warm median / P95 ms，n=20 | 30.910 / 35.649 | 29.372 / 32.693 | P95 -8.29% |

本次warm P95无>20%增长，不需要用不同case集合解释性能。不是可靠的速度改善宣称，样本小且未flush OS cache。31/31成功query cases（含5 unsupported诊断）cold/warm alias ranking稳定；capacity case6次expected error空ranking也稳定，独立计数。总32cold160warm，另有5scope controls（3新增Documents、2既有History）；不把control混入latency。真实Agent/Host端到端延迟未测。

## 验证与采用决策

TDD：新增native scope tests首先19 failed（missing kwargs / MCP schema），随后19 passed；增补真实999页命中与无read capability的forced MCP调用后20 passed。Runner capability test先1 failed，修复native mapping后通过。相关Document+retrieval第一次targeted112 passed、2 skipped、1 failed：旧unscoped test double不接受新增None kwargs，按兼容合同修复runner为无scope时仍原调用；最终retrieval/evaluation28 passed、2 skipped，scope20 passed；已有document域66 passed。两skip为未显式提供真实QMD路径的独立验收test，本次唯一候选run已经显式真实QMD且ready；不将skip称PASS。git diff --check通过。未跑full suite，不降低restore空间guard。

采用依据：三个冻结case的具体不可表达scope已被原生合同覆盖，排除4个越范围命中且保留4/4gold；共同23case质量/定位/排序没有回退，既有capacity安全仍拒绝。没有排名或索引改善证据，不另加候选，不改gold，不优化Study/WA排序，不扩展chapter/date。生产与native Host效果仍UNKNOWN，合成scope结果不外推真实教材质量。

## 共同集合的原始分母与定位计数

以下从两份raw按baseline supported IDs选择23case后使用未改scorer汇总；质量/locator两侧完全相等，仅latency不同。省略latency（上文相同case集合比较，完整samples在外部raw）。不混入新增case，不改旧P15文档。

```json
{
  "groups": {
    "study/study": {
      "cases": 6,
      "quality_supported": 6,
      "unsupported_cases": [],
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
      "cases": 4,
      "quality_supported": 4,
      "unsupported_cases": [],
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
      "cases": 7,
      "quality_supported": 7,
      "unsupported_cases": [],
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
      "cases": 5,
      "quality_supported": 5,
      "unsupported_cases": [],
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
    "all": 23,
    "quality_supported": 23,
    "unsupported": 0,
    "errors": 0,
    "contract": 0,
    "contract_pass": 0
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

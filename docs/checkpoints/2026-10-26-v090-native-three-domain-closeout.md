# v0.9.0 三域原生链路收尾证据

**三域业务及联合检索均已有真实模型与当前 Desktop 原生 Gateway 证据；最终提交的全量Checks与实际Tag/Release仍须分别核验。** 全部素材均自造，仅使用原隔离 alias，不是生产数据验收。四个有界任务分别运行；不将它们称为原四任务失败报告的同轮 PASS，历史失败和待审结果全部保留。

## Host、来源和首次部署

独立重启助手通过 Windows 管理服务启动，并以安装清单中的应用标识重新激活 Desktop；助手返回 relaunch-requested 后，另由真实原生 health/workflow 和新工具发现确认重载。隔离 Host instance=`c09c4cba5f8848a29080cce8889cf3b0`，startup_source_sha256=`39ed37bf96116a439afdab2510b7fa61277587aa3d466991f064a23243b89a9d`，对应首次部署修复源码。

当前 Host 发现并实际执行 admin-only `initialize_history({})`，返回 first_deployment、sources=0/items=0。随后同一固定 manifest 经原生工具导入1条来源、errors=0，再规范化为1条 canonical 消息；这与此前 imported=0/errors=1 的失败回执不同。

- 来源：`source-file:b3125e3eeea3783f9ce977fe109363b02e30651c01da5eded78ae2e2ca9f1ccd`
- 原字节：258 bytes；SHA-256=`6d06681bdf377d53ab0d26463c925ed29831ad672a14f5f041f98aa4f581ef0d`
- 消息：`message:0f75a2232762669186ba88c717ecbea64c23a0b58e27f604173cf927b99e4fc3`
- `verify_history_source` verified=true；`verify_canonical_history(reparse=true)` verified=true、messages_verified=1、source_outcomes_verified=1、diagnostics=0。
- canonical SHA-256=`17680553173c68b99e5c58706f3f3c0a0079f28078e3ccd54589b931bdbc6e14`，模型任务后再次重解析核验不变。

## 当前原生业务证据

真实执行器统一为 `deepseek-harness-no-tools/0.2.1-rc.3.1`，请求和供应商返回型号均 `deepseek-flash`。本地请求桥只将固定 handler 的请求交给本聊天实际原生 MCP 工具，回送真实 structuredContent；不启动 SDK Gateway、CLI 或测试 Host。

| 任务 | 状态 | 独立验证 |
| --- | --- | --- |
| Study | COMPLETED / verified=true | 既有分析v1再次通过当前原生bundle读取，与完整写回执逐字段相等 |
| History `native-history-20261026-v2` | COMPLETED / verified=true | 派生摘要经commit/verify两次原文回读；原文、时间、hash未改，摘要不冒充原始History |
| Memory `native-memory-20261026-v2` | WAITING_FOR_REVIEW / verified=true，error=null | 真正写入pending历史候选，独立fetch candidate和原消息一致，未自动approve/commit为正式事实 |
| 检索 `native-evidence-20261026-v3` | COMPLETED / verified=true | 引用选择及模型notes保留，再次原生retrieve_evidence回读一致；三域status=ok，Memory零命中 |

Memory候选为 `candidate:76ea81e5199e4d43b1fc7e2d3c83dcbd`，subject=`invented-user`、classification=`historical_statement`、confidence=.95、state=pending。当前原生候选列表total=1；独立完整性verified=true/complete=true，正式facts=0/versions=0。pending候选不进入正式Memory检索，零命中不是故障或现实事实不存在的证明。

Study分析：`wrong-analysis://sha256/cc017f211f3f2b45e68e59270d2edf2893fe14480093b41987180565d7a5c00d`，version=1；来源与[此前原生Study检查点](2026-10-26-v090-native-resume.md)一致，没有重新注册或重复分析写入。

### 摘要证据

| 任务 | 请求SHA-256 | 响应SHA-256 |
| --- | --- | --- |
| History v2 | `69e4f5de03fc9ac6908da61f2584c61ebd97f552ef5a23b82a496662b00b9e83` | `462ebeb283067a9c124a9f7b93b24c7dee1347e88a82bf08b38d42fd15f173c6` |
| Memory v2 | `f04e2caef5add0cab0809aefab77e00ab4cf7486f014c67974662c8c937a0cb8` | `d7d9c2bf5b8024c59bcadca1e04f59564b6f8ec15ddd7cd55f7116f17195d98a` |
| 检索 v3 | `0645cb2841b6e595c0980e790d2c94d57b2f9633b00743828a4a812dee80b190` | `6511ad330c62008d5ceb341218f736c89f76f99226e15f7840737806fd8eb580` |

Memory Result摘要=`a14bf9b4739774003e0faf7a67d2d8891582cc37138c23b5b6ca105c2a7de6ab`，Gateway回执摘要=`7017a3ece4fafb36da60207dfdd881c114012339ff5762b7d8323005dd2efe74`。History回执摘要=`e2f3f3a9f0b48039c6987835b1e4756d40d861cfbc5487e9cee50c46c009941c`；检索回执摘要=`acbf3d443c1dd4fe0a0b86b2a91258ed7e3aecb12e23c312d1d8486c1f4b3ea2`。这些是受信本地调用链的关联摘要，不是不可伪造的外部身份认证。

## 具体问题和最小修复

Memory v1合法Result明确报告缺先前状态和第一人称主体未标识，故仍待审、未写候选。新增可选可信Task `proposal_scope={subject, classification:'historical_statement'}`：只将任务限定为指定reported别名的历史原话，不要求判定变化、当前性或认证身份。handler从Task重复检查绑定，模型和源正文均不能改成当前fact_update。原文、发生时间、confidence、uncertainties及人工审批规则不变。B在新任务中自行返回uncertainties=[]后才写pending候选；不是修改旧Result。

检索 v1/v2将已知历史/零命中限制放入uncertainties，原门槛正确拦截。契约原本只允许selected_refs，却要求把这些限制放在检索不存在的summary/analysis/basis中。新增有界可选notes保留模型报告限制，并连同引用在commit/verify核对；旧selected_refs输出仍兼容。没有自动从uncertainties搬运、过滤或清空内容。v3由真实B自行生成notes且无阻塞不确定性，原门槛不变。

History v1由人工桥接在verify阶段迟于60秒回应而超时，保存PARTIAL/TIMEOUT；不是模型失败或原文损坏。v2采用连续原生请求泵，在原60秒预算内完成，没有增加timeout或重写v1结果。三个成功任务同Task重放均未发生新的模型/原生请求，候选仍同一ID且total=1。

旧 INVALID_RESPONSE 的失败原始包未保留，根因仍UNKNOWN，不能回填成上述语义问题或token预算。当前安全诊断确认新任务JSON/绑定有效；错误处理和待审门槛保留。

## 验证和剩余发布工作

新增范围测试6项先红后绿；检索notes篡改/类型/长度回归先红后绿。主线程编排组合117 passed；独立范围复核66 passed加5项隔离探针，独立notes复核77 passed，另证实即使有notes，真实非空uncertainties仍进入待审且业务写入为0。未发现新增P1/P2。

所有当前真实业务验收均为自造文本资料；没有私人数据、图像能力或生产恢复验收声明。后续仍须冻结最终提交，完成全量Windows/Ubuntu CI、构建/隐私审计/干净安装、复核GitHub流程后才正式发布。此前各阶段PARTIAL和失败记录保持原历史范围，不由本页覆盖。

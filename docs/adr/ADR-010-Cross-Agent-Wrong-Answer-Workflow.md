# ADR-010: Cross-Agent Wrong Answer Workflow

状态：Accepted
日期：2026-09-26

## 背景

P7–P9 已实现错题 source、Agent analysis、版本 provenance、MCP tools 和原始 Asset/Document resources，但 MCP 工具清单没有说明自然语言错题请求如何串接能力。把完整流程放进一个 Codex Skill 会令其他 MCP Agent 看不到规则；把它复制进 MCP description、README 和 Skill 则会造成规范漂移。

## 决策

- 唯一 canonical workflow 正文存于随 Python distribution 发布的 `src/cognivault/workflows/wrong_answer.md`（改名前路径为 `src/chatgpt_study_system/workflows/wrong_answer.md`）。
- MCP 以固定 URI `study-workflow://wrong-answer` 暴露该正文为 Resource；`wrong_answer_workflow` Prompt 动态复用相同正文，不存副本。
- MCP 错题相关 tools 的简短 description 负责自然语言请求路由，并指向 canonical URI。MCP Agent 即便不支持 Skill 或 Prompt，只要支持 tool/resource 发现与读取，也能发现流程并使用工具。
- Resource 用途不依赖私有数据库数据。持久化仍受 Gateway capability 控制；Agent 可以分析但只在用户明确要求保存时写入。
- Codex Skill 不是 Core 前置条件；若以后添加，只能作为加载 canonical MCP Resource 的薄适配，不允许副本流程。
- 原始 JPG/PNG 或原始教材页视觉证据优先；OCR/文字层仅作确定性 derived data 并须核验。Agent 负责题意、作答、批改、答案与错因分析。Study/教材查询按需。Gateway 继续只做确定性存取、校验和持久化。

## MCP 能力解释

MCP Tools 是模型可发现和调用的操作，适合在简短用户请求下自动选用。Resources 是客户端管理上下文，Prompts 是用户/客户端选择的模板；后二者帮助发现和显式启动，但协议不保证 Host 自动注入或自动选择。因此不把 Prompt 当作 minimal-prompt 自动触发的唯一机制。每个 Agent Host 的实际体验仍受其工具策略、图像附件桥接、用户审批设置和可用 capability 影响。

## 后果

- 所有宿主共用同一版规则，不需在 Gateway 引入意图分类器或模型。
- “分析”默认只在对话中输出；“保存”意图明确时才调用写工具，防止 Agent 因惯例而产生用户未要求的持久副作用。
- 当前资产注册工具需要调用者能提供原始字节。Host 若只暴露文件名、无法读取用户附件或无法把图像交给 Agent，则工作流应停止并说明需要附件访问，不能重建或替换 source。
- 原始 Asset 注册、错题 source 注册和分析版本保存是分开的 Gateway 调用/事务；若中途失败，Agent 必须准确报告已成功的部分，不能把部分写入说成完整保存。
- 自动分析 workflow 可被成功接线测试证明，但任意第三方 Host 的一行自然语言行为无法仅通过 Gateway 的协议测试保证；需要对应 Host 的真实附件/工具 E2E 才能认证该体验。此任务不执行真实图片测试。

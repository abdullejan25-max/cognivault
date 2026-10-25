# v0.9.0 Agent Orchestration 候选实施计划

用户明确授权自主实施、真实合成调用及可恢复的本地注册修复，不设置逐步确认。发布仍受全部真实链路门槛约束。

## 当前事实检查清单

- [x] 工作树位于原隔离 checkout；初始干净，分支 codex/agent-orchestration，HEAD 3619610。
- [x] 获取远端后 main 仍为 a8f12d5；67dd22b、3619610 均仅在 PR #22，尚未合并。
- [x] PR #22 OPEN/DRAFT/MERGEABLE，无 reviews/comments；3619610 的 push/PR 两轮 Windows/Ubuntu CI 均 SUCCESS。
- [x] 当前 Host 直接读取已知 workflow 仍 unknown MCP server；不是 resources/list 误判。
- [x] 包 metadata 初始 0.8.0，标准 MCP 及 Harness 受限执行仍缺真实证据。

## 设计选择

复用现有 Engine / domain handlers，修复取消、可用性和错误收敛；扩展固定标准 HTTPS 无工具适配器，而不重写 Gateway。备选方案是 Harness 自定义受限 profile 或 Codex 子 CLI；前者需验证工具与配置继承隔离，后者也不能只靠 read-only/prompt 宣称无工具。真实凭据不可用时固定报告认证阻塞，禁止从 GUI profile 提取秘密来制造通过。

任务计划由主控 Agent 提供可信、明确的有向依赖表；SDK 校验唯一标识/依赖/环路，逐任务运行，不从模型输出取得权限或 Gateway 工具名。失败依赖阻断后继；独立任务继续。注册器增加可信配置的成本/延迟估计及停用能力，不声称估计是实际计费数据。

## 实施步骤

1. 注册与真实执行器：读取实际配置/程序入口，备份本地变更；标准 HTTPS 适配器先写 MockTransport 故障测试再实现；只有真实网络执行才记录真实模型证据。Desktop 当前 Host 缺入口时不以测试 Host 代替。
2. 内核修复：新增 tests/test_orchestration_v090.py，先复现 COMMITTING 取消后仍可能启动写入、无可用 agent 不产生回执、拒绝/阻塞错误丢失；然后修改 models/engine/adapters，保持旧请求 digest 可恢复。增加可信计划与依赖失败测试。
3. 三域及候选版本：用同一 Engine/Port 对隔离合成 Gateway 回归；补全自动化验收入口，只写临时合成数据。统一 0.9.0 metadata、lockfile、版本断言与动态包审计；文档明确候选，不创建 tag/Release。
4. 独立第二轮审查：对全部改动检查错误/取消/恢复/隐私/安装，修复后 targeted + 完整 pytest -q -rs、lock、compile、build、privacy audit、干净安装；提交更新 PR，等待精确提交全部 CI。真实模型、Desktop、安全门槛全部 VERIFIED 才合并/tag/Release；否则保存候选与具体解除条件。

## 验收证据

发布清单见 docs/v0.9.0-release-gates.md；真实执行器与 Desktop 的 CONFIGURED / CONNECTED / VERIFIED 单独报告。不得把开发子 Agent 调用、MockTransport、isolated MCP SDK 或已配置文件计为产品真实端到端通过。

# Memory 日志文件并发校验修复设计

用户已授权修复测试发现的 Memory 并发问题，并在确认属于项目通用问题后发布 v0.9.1。原始全量及复跑失败保留于 `docs/checkpoints/2026-10-31-project-test.md`。

## 问题与方案

`SQLiteMemoryStore._validate_path` 对同一候选文件依次执行 `exists`、`is_file`、`stat`。SQLite DELETE journal 可在这些读取之间正常消失，产生误报 `STORAGE_UNAVAILABLE`。该检查模式本身存在时间差，不依赖个人配置；增加确定性回归，并在 Windows / Ubuntu CI 上验证修复。

采用一次 `lstat` 快照判断叶子文件的普通文件类型、硬链接数和 Windows reparse 属性。父路径仍由现有重解析点检查器检查；仅叶子文件的 `FileNotFoundError` 按原有“当前不存在”语义处理，其余错误继续拒绝。已经取得正常快照后文件消失不触发额外类型/存在性读取。

不采用简单忽略所有 `OSError`，会放宽权限错误处理；不采用进程内锁，不能覆盖独立 Gateway 进程。该修复不宣称消除所有恶意路径替换竞态。

## 边界与验收

- 不改 schema、写入事务、幂等键、版本冲突、WAL 拒绝、权限或公开 API；不读写真实数据。
- 回归在真实临时文件上精确注入 journal 在叶子元数据读取前/后消失的时序，断言实际 Memory 写入和回读。
- 保留硬链接、非普通文件、重解析点、父路径和访问错误拒绝；安全拒绝不得创建数据库。
- 原始并发用例执行有界重复；本地全量测试、双归档构建/审计、干净安装/双 stdio、精确提交 Windows/Ubuntu CI 通过后发布。
- 当前 Desktop 原生 Gateway 未注册，与代码修复及合成验收分列，不冒充生产或 Host PASS。

## 发行范围

更新包版本、lock 和当前版本断言为 0.9.1；新增补丁发行说明并更新 README / current-state 的当前入口。历史版本说明、合成 fixture 中的旧版本及首次失败记录保留。发布经 PR 合并和精确 SHA 检查后创建 tag / GitHub Release，不推断包索引也已发布。

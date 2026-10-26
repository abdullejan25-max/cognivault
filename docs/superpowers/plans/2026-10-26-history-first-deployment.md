# History 首次部署缺口的最小修复

## 事实与范围

当前原生隔离Host已重载，固定manifest通过allowlist，但唯一条目返回HISTORY_UNAVAILABLE。SQLiteHistoryBackend._connect默认不创建缺失库，MCP没有正式History初始化入口。不能以直接Agent数据库操作或生产库复用绕过。

本方案沿用已授权v0.9.0任务和现有Memory首次部署边界：增加显式、仅管理员、仅配置SQLite History路径的initialize_history。它不是数据迁移，不接受路径或原始业务数据，不在普通检索或导入中自动触发。独立故障注入随后证实原Memory首次初始化也存在下述journal竞态，本轮一并修复其首次创建过程，既有库与业务schema不变。

## 方案选择

直接复用原History.initialize会接纳已有数据库，不满足首次部署安全边界；自动导入时建库会引入隐式副作用。采用独立initialize_empty，原History.initialize语义不变，不抽公共存储框架。数据库及-wal/-shm/-journal必须全不存在（含brokenlink）；独占保留四个句柄并核对文件身份，失败不删除残留或替换别人文件。

审查实证表明：先在最终路径打开SQLite建表，即使事后检查，也可能让SQLite删除建表前被并发替换的外来journal。修正策略为在:memory:完成原schema/候选schema、commit并serialize；写入前检查namespace，随后仅通过原持有的数据库fd完整写入/fsync，写入后再验证身份、链接数和尺寸。SQLite始终不接触最终路径或其辅助文件。serialize不可用、短写、替换等故障均fail-closed且保留现场。

## 执行与验证

1. 真实临时Gateway/MCP测试先复现缺少API；覆盖空库、已有主库/辅助文件字节不变、重入、并发、失败保留、路径拒绝。
2. 存储层实现strict首次部署，Gateway再校验admin/配置，MCP仅admin可发现且定向执行仍检查权限，严格空参数。
3. 用真实临时Gateway经MCP执行首次部署→源导入→规范化→独立回读，验证业务链路；这不是Desktop证据。
4. 独立安全复核，相关回归、完整跨平台CI、构建安装与隐私检查。最终Host重载后，仅对原隔离alias执行原生初始化及验收。生产配置和权限不变。

## 仍未通过的门槛

初始化修复本身不等于Memory候选验收，也不解释旧INVALID_RESPONSE；不降低模型置信度或不确定性要求。未达完整真实/原生发布门槛前保持草稿，不合并、不tag、不Release。

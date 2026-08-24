# 数据边界

自动化获客网页是唯一业务操作入口，PostgreSQL 是唯一事实来源。

客户、线索、邮件、回流、待办、生命周期、权限、审计和数据飞轮均以 PostgreSQL 记录为准。项目不使用飞书 Base、CSV 或其他外部表格维护第二份业务状态。

## 明确禁止

项目禁止访问 AI 投资 Base：

`https://ncnqnih15n0h.feishu.cn/base/CpnybxXoGasunts8O4UckKFyn5b`

该项目不读取、不写入、不同步该 Base 的任何表。共享 HTTP 客户端已经加入硬拒绝，命中该 Base URL 或 Base token 时会在网络请求前抛出错误。

## 数据飞轮

获客数据飞轮直接读取 PostgreSQL 中的触达结果、客户回复、销售跟进和成交结果，并将策略快照与学习审计写回 PostgreSQL。飞书不参与同步、审批、催办或学习。

CSV/Excel 仅作为导入和导出格式，不能作为运行时主数据源。

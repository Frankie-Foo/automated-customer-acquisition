# 数据边界

自动化获客项目的业务数据源是 PostgreSQL。

## 明确禁止

项目禁止访问 AI 投资 Base：

`https://ncnqnih15n0h.feishu.cn/base/CpnybxXoGasunts8O4UckKFyn5b`

该项目不读取、不写入、不同步该 Base 的任何表。共享 HTTP 客户端已经加入硬拒绝，命中该 Base URL 或 Base token 时会在网络请求前抛出错误。

## 获客数据飞轮

获客飞轮如果需要使用飞书 Base，只能使用独立的获客 Base，并且不能复用 AI 投资 Base 的 token、URL 或表 ID。当前独立 Base：

`https://ncnqnih15n0h.feishu.cn/base/S7bGbt24Kazk3usbwQecVviZnDe`

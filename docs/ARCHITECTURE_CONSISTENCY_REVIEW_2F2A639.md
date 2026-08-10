# 企业化架构一致性审查：2f2a639

- 审查基线：`2f2a6393460e210c58e232c991d15b954d1fc10c`
- 审查范围：领域边界、迁移 031—034、HTTP/MCP API 契约、跨模块依赖
- 结论：**P0 未清零前不应合并到企业化主线。**
- 动态证据：`python -m pytest -p no:cacheprovider -q`，256 项通过。
- 边界声明：本次只审查本地仓库；未访问任何飞书 Base，未读取 `.env`，未检查或实现 ContactOut 多账号轮换。

## 严重级别

- P0：违反硬性数据边界、身份隔离或所有权契约，必须在合并前解决。
- P1：会造成错误决策、配额失效、任务永久卡死或难以演进，应在上线前解决。

## P0

### P0-01｜PostgreSQL 没有强制 10 人数据隔离

**证据**

- `src/sales_automation/db.py:41-56` 所有请求、后台任务和管理员操作共用同一种数据库身份，连接事务没有注入当前用户或角色。
- 迁移 031—034 均未启用 RLS，也没有策略；既有 `contacts`、`leads`、`interactions`、`outreach_messages` 等核心表同样依赖调用方手工拼接 owner 条件。
- 多个 service 接口只接收 `contact_id`，最终 UPDATE 不带 owner 条件，例如 `LifecycleService.update`、`ProfileAgentService.summarize`。

**影响**

任意新接口、后台调用或漏传 `user` 的仓储方法都能跨用户读写。应用层过滤不是企业级租户边界，无法满足“10 人权限隔离”。

**最低整改**

1. 应用运行账号不得是表 owner，也不得拥有 `BYPASSRLS`。
2. 每个请求事务使用 `SET LOCAL app.user_id`、`app.role`；后台任务使用显式 system principal，而不是隐式管理员。
3. 对联系人、线索、任务、草稿、触达、互动、活动、计划及其运行记录启用并 `FORCE ROW LEVEL SECURITY`。
4. 写 SQL 同时保留 owner 谓词，形成纵深防御；RLS 不替代服务层授权。
5. 加入两个销售身份交叉读写的数据库集成测试，覆盖 HTTP、MCP、scheduler。

### P0-02｜MCP 把“可查看公共池”误当成“可写”

**证据**

- `src/sales_automation/db.py:957-973`：`get_contact_for_user` 对销售返回“本人或公共池”，`get_private_contact_for_user` 才是可写私域判断。
- `src/sales_automation/mcp_server.py:107-140`：`update_customer_stage` 与 `generate_customer_profile` 使用前者做检查，随后调用不携带 principal 的写服务。
- `src/sales_automation/services/lifecycle.py:40-49`、`src/sales_automation/services/ai_agents.py:20-30,65-66`：最终按 `contact_id` 直接修改。

**影响**

任意销售可经 MCP 修改尚未认领的公共联系人；检查后发生所有权转移时也存在 TOCTOU 越权写。HTTP 的私域写约束与 MCP 契约不一致。

**最低整改**

- 所有写用例接收不可为空的 `Principal`，并在单条 UPDATE 中校验 owner/pool；公共联系人必须先原子认领。
- MCP 写操作改用 `get_private_contact_for_user`，管理员例外必须显式记录审计。
- 增加“销售 A 不得修改公共池/销售 B 私域”的 MCP 契约测试。

### P0-03｜获客计划 owner、配额与结果所有权互相矛盾

**证据**

- `migrations/033_acquisition_plans.sql:8` 强制每个计划有 `owner_user_id`。
- `src/sales_automation/services/acquisition_planner.py:79-100` 使用该 owner 执行搜索并扣其个人配额。
- `src/sales_automation/linkedin_public_search.py:231-237` 搜索任务归该 owner，但 `:467-470` 将晋升联系人固定写入 `pool_type='public'`，不写 owner。

**影响**

个人额度产生的结果进入公共池，可被其他销售认领；计划、任务、配额、联系人四个聚合的所有权链断裂，审计无法解释“谁付出额度、谁拥有线索”。

**最低整改**

先记录 ADR，只允许二选一：

1. **用户计划**：结果原子写为该 owner 的 private 联系人，并保留 assignment source；推荐。
2. **组织公共计划**：计划 owner 改为 requester/auditor 语义，使用组织配额，结果进入公共池。

不得继续保留“个人扣额 + 公共结果”的混合契约。

### P0-04｜禁访 Base 只在部分 HTTP 路径生效

**证据**

- `src/sales_automation/http.py:20-23,40-52` 仅检查初始 URL；标准 `urlopen` 会自动跟随重定向，重定向目标没有再次校验。
- `src/sales_automation/email_discovery.py:153-154,188-189,471-472`、`src/sales_automation/linkedin_public_search.py:1190-1193`、`src/sales_automation/pdca_sso.py:45-52`、`src/sales_automation/vps_sso.py:71-78` 直接调用 `urlopen`，完全绕过共享守卫。

**影响**

配置、公开网页或允许域名的重定向仍可能触达被禁止资源，违反绝对数据边界。

**最低整改**

- 所有出站 HTTP 统一经过 policy-aware transport；禁用裸 `urlopen`。
- 每次 redirect 前重新验证 scheme、host、path 和禁止标识；限制跳转次数。
- 在配置加载、应用 transport、网络出口三层阻断；测试直接 URL、大小写/编码变体和多跳重定向。

## P1

### P1-01｜缺少公司主实体，统一领域边界尚未成立

`migrations/001_init.sql:10-33` 把公司名称、域名、规模、融资、行业、地点放在 `contacts`；`migrations/027_unified_pdca_closure.sql:48-71` 又在 `leads` 保存公司域名、国家和地区。迁移 031—034 只增加计划、学习和缓存，没有建立 `companies` 聚合。

同一公司多个联系人会复制并漂移公司属性，域名去重、账户级研究、公司生命周期和多人协作都没有稳定主键。

**整改**：建立 `companies(id, normalized_domain, canonical_name, ...)`，以规范化域名作为可空唯一业务键；`contacts.company_id`、`leads.company_id` 外键关联。先双写和校验，再回填、切读，最后通过兼容视图淘汰重复列。

### P1-02｜样本不足时反而停用最后一个有效策略

`src/sales_automation/services/flywheel.py:97-99,113-121` 明确声明样本不足“不改变既有策略”，并生成 `insufficient_sample` 快照；但 `src/sales_automation/db.py:3027-3062` 在插入任何新快照前先把当前 active 改为 superseded。结果是一次低样本运行会让 `get_active_flywheel_snapshot` 返回空。

**整改**：不足样本只追加 observation，不替换 active；active 切换应在同一事务内以 CAS 完成，并保留 last-known-good。

### P1-03｜实验发送数被 JOIN 放大，可提前选错赢家

`src/sales_automation/db.py:3255-3273` 将每封 `outreach_messages` 与该联系人发送后的全部 reply interactions 连接，再用 `COUNT(*)` 计算 sent/delivered/replies。一个联系人多条互动会重复计算同一封邮件。`src/sales_automation/outbound_quality.py:489-491` 以每变体 100 封作为自动选赢家门槛，因此可能虚假达标。

**整改**：按 provider message/draft 明确事件归因；消息与互动分别预聚合后再 JOIN，发送量至少使用 `COUNT(DISTINCT om.id)`。增加“一封邮件、多条互动”回归测试。

### P1-04｜学习变更与审计事件不是一个事务

`src/sales_automation/services/flywheel.py:267-321` 先调用阈值/赢家更新，再单独插入学习事件；对应仓储方法各自开启事务。崩溃会留下无审计变更；manual flywheel 与 scheduler 并发时可能重复改版本、重复事件。

**整改**：提供单个仓储命令，在同一事务中完成条件更新、证据校验和事件插入；事件加入 `decision_key` 唯一约束，更新使用 expected version/CAS。

### P1-05｜获客运行无租约，崩溃后当天永久卡住

`migrations/033_acquisition_plans.sql:25-32` 每计划每天只有一条 run；`src/sales_automation/db.py:1785-1791` 只允许 failed 状态重试。进程在写入 running 后崩溃，该计划当天无法重新领取。

**整改**：加入 `lease_owner`、`lease_expires_at`、heartbeat、attempt；通过单条 SQL 原子领取 queued/failed/stale-running。完成操作必须校验 lease token。

### P1-06｜获客先产生副作用，后扣配额

`src/sales_automation/services/acquisition_planner.py:85-100` 先执行外部搜索、写 search result/contact，再调用原子配额消费。并发消耗配额时，最后的消费可能失败，但外部成本和数据库结果已经产生。

**整改**：调用前原子预留用户与全局额度，结束后结算实际用量并释放余额；reservation 需要幂等键并与 plan run 关联。

### P1-07｜“每日”语义依赖数据库会话时区

`migrations/033_acquisition_plans.sql:25`、provider 统计和用户/全局配额均使用裸 `CURRENT_DATE`。若数据库会话是 UTC，而业务日按 Asia/Shanghai，00:00—08:00 的运行、配额与统计会落到错误日期。

**整改**：ADR 明确业务日时区；写入显式 `((NOW() AT TIME ZONE 'Asia/Shanghai')::date)` 或由统一 business-clock 函数生成。所有 daily 表使用同一函数。

### P1-08｜新增 HTTP API 没有稳定错误与幂等契约

`src/sales_automation/web.py:417-433` 的解析、必填和范围错误由 `web.py:1395-1403` 统一映射为 500；只有 provider budget 映射 429。创建计划没有 idempotency key，客户端超时重试会重复创建；手动运行是同步长请求，超时后结果状态不明确。

**整改**：统一错误 envelope 与映射：认证 401、授权 403、校验 400/422、冲突 409、配额 429、依赖 502/503；创建命令接受 scoped idempotency key；运行命令返回 durable run id，并由查询接口读取状态。

### P1-09｜接口层穿透仓储，principal 与事务边界无法统一

`web.py` 同时承担路由、授权、输入解析、审计和事务编排，并在 `src/sales_automation/web.py:1023-1031` 直接打开数据库连接。`Repository` 汇集身份、获客、联系人、配额、触达、学习等所有持久化操作；service 广泛接收 `Any`/`hasattr`，契约只能靠运行时猜测。

**整改**：按 bounded context 拆分窄端口，不要求立即拆包：

- `IdentityAccessPort`
- `AcquisitionPort`
- `CompanyContactPort`
- `LeadLifecyclePort`
- `OutreachPort`
- `FeedbackLearningPort`
- `ProviderGovernancePort`

HTTP/MCP 只调用 application command/query；principal、transaction、audit context 由 command bus/use-case 入口统一创建。

## 迁移 031—034 逐项结论

| 迁移 | 结论 | 上线前补强 |
|---|---|---|
| 031 data flywheel | 顺序正确，基本可重复执行；并发激活与 last-known-good 不安全 | status/scope CHECK；窗口与样本 CHECK；CAS 切 active；RLS |
| 032 automatic learning | 对 030 的依赖顺序正确；审计事件缺少幂等与原子性 | decision key UNIQUE；actor/run id；事务化 command；RLS |
| 033 acquisition plans | FK、范围 CHECK、每日唯一约束基本合理；缺租约与明确业务日 | lease/attempt；业务日函数；所有权 ADR；RLS |
| 034 provider cache | 主键与过期索引可用；`credits_reserved` 当前没有读写闭环，status/credits 无 CHECK | 删除未用列或完成 reservation 语义；CHECK；清理策略；RLS/管理员策略 |

迁移执行器按文件名排序，因此 031 → 032 → 033 → 034 顺序本身没有发现 P0/P1；四个迁移在同一数据库事务内执行。风险来自数据边界和运行语义，不是文件排序。

## 建议 ADR

1. `ADR-001 Principal 与 PostgreSQL RLS`：请求身份、system worker、管理员、RLS 变量和连接池清理。
2. `ADR-002 获客计划结果所有权`：用户私域计划与组织公共计划二选一。
3. `ADR-003 公司/联系人/线索聚合边界`：公司主键、去重、生命周期归属、兼容迁移。
4. `ADR-004 出站网络数据边界`：统一 transport、redirect policy、域名/IP/路径校验。
5. `ADR-005 学习决策事务`：证据快照、版本、幂等键、人工回滚、last-known-good。
6. `ADR-006 Business day`：Asia/Shanghai 的 daily quota/run/statistics 语义。

## 推荐实施顺序

1. 先封禁全部网络绕行与 redirect 绕行（P0-04）。
2. 修 MCP 写权限，并让 principal 贯穿写用例（P0-02）。
3. 引入数据库事务身份和 RLS，补双用户集成测试（P0-01）。
4. 决定获客计划所有权并修正落库/配额契约（P0-03）。
5. 增加 run lease 与 quota reservation（P1-05、P1-06）。
6. 修实验计数、学习事务和 last-known-good（P1-02—P1-04）。
7. 建公司主实体并渐进迁移（P1-01）。
8. 统一 business day 与 HTTP API 契约（P1-07、P1-08）。
9. 最后收敛 ports；在上述行为稳定前不做大规模目录重构（P1-09）。

## 合并门禁

- P0-01 至 P0-04 全部有代码、迁移及集成测试证据。
- 两个销售身份交叉访问：私域读写均拒绝；公共池只读，认领原子。
- 所有出站请求及每次 redirect 都经过同一策略，并有禁止资源回归测试。
- 计划 owner、配额 owner、任务 owner、联系人 owner 的关系由 ADR 和测试固定。
- flywheel 低样本不替换 last-known-good；学习决策与审计原子且幂等。
- API 错误状态与幂等重试通过契约测试。

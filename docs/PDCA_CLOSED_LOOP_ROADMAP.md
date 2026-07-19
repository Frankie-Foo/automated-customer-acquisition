# PDCA 获客闭环实施说明

## 目标

自动化获客不只负责找名单，而是把线索从进入、清洗、分配、触达、跟进、成交到复盘全部串起来。

第一版验收标准：

1. 新线索进入后保留原始来源。
2. 系统自动去重、清洗、分配和评分。
3. 生成客户画像、跟进任务和触达草稿。
4. 销售确认后发送，邮件打开、回复、退信能回流。
5. 回复客户进入生命周期跟进。
6. 活动、渠道、销售结果进入 PDCA 漏斗和 ROI 统计。

## 当前系统映射

| 业务对象 | 当前主表 / 新增表 | 说明 |
| --- | --- | --- |
| 正式客户档案 | `contacts` | 继续作为主客户表，不再另建重复主库。 |
| 客户画像 | `customer_profiles` view | 基于 `contacts` 输出统一画像视图，兼容后续 UI 和报表。 |
| 原始线索 | `leads` | 记录 Excel、官网、Odoo、Webhook、搜索等入口的原始线索。 |
| 触达记录 | `outreach_messages` | 记录邮件、WhatsApp、LinkedIn 私信草稿、发送状态和个性化证据。 |
| 客户互动 | `interactions` | 记录邮件、电话、会议、WhatsApp、人工备注等行为。 |
| 跟进任务 | `followup_tasks` | 支撑今日任务、沉默提醒、报价后跟进、成交后转介绍。 |
| 获客活动 | `campaigns` | 记录国家、渠道、产品线、负责人和预算。 |
| 活动指标 | `campaign_metrics` | 记录线索、有效联系方式、发送、回复、会议、报价、成交、成本和收入。 |
| 权限隔离 | `sales_users` + `contacts.owner_user_id` | 销售只看自己，管理员看全部。 |
| 邮件回流 | `email_events` + `interactions` | 保留现有回流，后续同步到互动时间线。 |

## 分阶段落地

### Phase 1：统一闭环数据底座

已新增迁移 `027_unified_pdca_closure.sql`：

- `leads`
- `customer_profiles`
- `interactions`
- `followup_tasks`
- `outreach_messages`
- `campaigns`
- `campaign_metrics`

同时新增 Repository 写入接口，方便后续业务流程接入。

### Phase 2：线索入口和去重

优先接入：

- Excel / CSV 名单导入
- 人工新建线索
- 官网询盘 Webhook
- Odoo 客户 / 联系人同步

每条线索必须保留 `source_type`、`source_ref`、`source_row`、`raw_data` 和 `campaign_id`。

### Phase 3：分配、评分和任务

线索入池后自动执行：

- 邮箱、电话、WhatsApp 标准化
- 邮箱、电话、公司域名去重
- 黑名单和退订名单过滤
- 国家、区域、语言补全
- 按区域、产品线、团队规则分配
- 生成 ABCD、意向分、客户价值分和原因说明
- 生成第一条跟进任务

### Phase 4：AI 触达草稿

第一阶段只做“AI 生成，人工确认发送”：

- 开发邮件
- WhatsApp 消息
- LinkedIn 私信
- FABE 产品介绍
- SPIN 提问
- 沉默客户唤醒
- 成交后转介绍

A 类客户默认必须人工确认。

### Phase 5：PDCA 经营闭环

首页和管理后台逐步补齐：

- 今日 / 本周新增线索
- 24 小时首次触达率
- 有效联系方式比例
- 回复率、会议率、报价率、成交率
- 平均转化周期
- 沉默客户率
- A/B/C/D 分布
- 渠道成本、单客成本、ROI

PDCA 输出：

- Plan：本周线索、回复、会议、报价目标
- Do：销售每日跟进任务
- Check：完成率与漏斗异常
- Act：转派、渠道调整、话术调整、优先级调整

## 关键原则

- `contacts` 是正式客户主库。
- `leads` 是原始线索，不直接等同客户。
- 重复线索要合并到同一个客户，但保留来源历史。
- 销售端流程必须少按钮、少概念，默认只看“今天该做什么”。
- 管理端看全局配置、配额、渠道、ROI 和异常。
- 自动发送必须受频率、配额、退订、回复停止规则限制。

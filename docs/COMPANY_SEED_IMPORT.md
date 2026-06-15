# 公司种子导入模板

用途：导入公司/店铺级清单，然后系统自动通过 LinkedIn 公网搜索找联系人，生成 LinkedIn 链接、邮箱候选和电话候选。找到 `valid` 工作邮箱后，可选择自动入队或自动发送邮件。

## 推荐 CSV 列

```csv
company_name,category,reason,website,job_titles,industry,location,phone,email
Luxepolis,二手奢侈品平台,印度二手奢侈品电商且调性匹配,luxepolis.com,"founder,owner,partner,VP,director,head,board member,c-suite",luxury resale,India,,
```

## 支持的中文列名

- `公司/店铺名称`
- `类别`
- `简短背调`
- `官网/联系链接`
- `职位`
- `行业`
- `地区`
- `电话`
- `邮箱`

## 自动流程

1. 解析每一行公司种子。
2. 按公司名、官网域名、职位列表生成 LinkedIn 公网搜索 query。
3. 解析公开搜索结果里的 LinkedIn 个人主页。
4. 补公司域名并生成邮箱候选。
5. 高置信候选按配置调用 Hunter/Prospeo 验证。
6. 只有 `personal_work + valid` 邮箱会写入正式联系人邮箱。
7. 如果开启自动入队，只会把有 valid 工作邮箱的联系人加入发送队列。
8. 如果开启自动发送，会复用现有发件池、退订链接、open tracking 和发送配额。

## 注意

- 公司官网、info/sales/contact/support 等通用邮箱只作为候选，不自动发信。
- 电话如果表里已有，会写入联系人；没有电话时会尝试从官网首页、contact/about 页面和 `tel:` 链接提取公开电话候选，命中率不保证。
- LinkedIn 搜索只使用公开索引结果，不登录 LinkedIn，不抓取登录态页面。

## Phone behavior

- If the imported CSV has a `phone` column, that phone is attached to the matched contacts from the same company seed.
- If the CSV does not have a phone, the importer tries the public company website home/contact/about pages and extracts visible phone numbers or `tel:` links as `unverified` phone candidates.
- Public phone candidates are not treated as verified. Sales should confirm them before using the number for calls or WhatsApp.

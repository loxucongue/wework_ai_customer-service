# 客户身份合同

- status: current
- owner: V3 runtime and platform integrations
- effective_from: 2026-09-07

## 统一概念

| 规范字段 | 含义 | 第三方常见字段 |
| --- | --- | --- |
| `corp_id` | 企微企业 ID | `corpId`, `wecomCorpId` |
| `wechat` | 接待企微账号 | `userWechat`, `user_wechat` |
| `external_userid` | 企微外部联系人 ID | `customerWechatId`, `customer_wechat_id` |
| `platform_customer_id` | 第三方平台客户记录 ID | `customerId`; 旧入参 `customer_id` 仅作同义别名 |
| `platform_user_id` | 第三方平台接待人员 ID | `userWechatId`; 旧入参 `user_id` 仅作同义别名 |
| `customer_add_wechat_id` | 客户与接待企微的加微关系记录 ID | `customerAddWechatId` |
| `conversation_id` | 本地或上游会话 ID | `conversation_id`, `session_id` |

## 强制边界

- 销售接触范围唯一使用 `corp_id + wechat + external_userid`。
- `platform_customer_id` 与 `external_userid` 不得互相补位；值相同或平台客户字段出现 `wm` 前缀均视为合同错误。
- 托管回复必须显式提供企业、接待企微、外部联系人、平台客户和平台接待人员五类身份。
- 平台客户资料查询可以验证或补充平台客户 ID 和加微关系 ID，但不得覆盖外部联系人 ID。
- `conversation_id` 不得直接等于任一客户 ID；上游未提供时生成稳定的 `conversation:v3:<digest>`。
- 客户画像、停止联系、SOP 进度和主动触达按销售接触范围隔离，不同接待企微之间不得共享。

## 持久化与冲突

- `customer_identity_links` 保存当前身份映射、来源、验证状态和冲突候选。
- 同一销售接触范围出现不同平台客户 ID 时必须保留冲突证据，不得静默覆盖。平台客户资料查询得到的已验证值可以替换未验证观测值；未验证请求不能覆盖或降级已验证值；两个已验证来源冲突时标记 `conflict` 等待处理。
- 旧表 `customer_id` 列在迁移期表示平台客户 ID；历史混用值只读，不得作为权威映射来源。
- 历史修复必须依据平台查询或已验证映射，禁止仅按格式猜测后批量覆盖。

## 查询合同

- 有 `external_userid` 时只按外部联系人字段匹配；否则只按平台客户字段匹配。
- 禁止使用 `(customer_id = ? OR external_userid = ?)` 接受一个无类型输入。
- 管理删除、停止联系和任务取消必须先解析为唯一销售接触范围；歧义时拒绝执行。

# V3 store detail booking close

- Type: production reply quality hotfix
- Base SHA: `d085b01cba6fbe3fff2006a618496f4b1bb5a3a3`
- Production baseline: `ai-paths-unified-20260906-153201-d085b01c`
- Goal: 已确认门店后的停车、营业时间等到店详情答清后，明确把时间问题连接到预约登记和 10 元预约金锁定活动名额，避免无目的追问。
- Non-goal: 声称已经正式预约、已经免费保留名额、自动发送预约金卡或新增交易权限。
- Ownership: Reply 门店 Prompt、销售业务规则及对应测试。
- Risk: 过早提预约金会显得强推；必须只在门店已确认、客户主动询问到店详情且没有新卡点/暂停/投诉/风险时使用。
- Completed: 门店详情答复后将时间偏好直接连接到预约登记，并说明10元预约金锁定活动名额；不使用“先记一下、后续再登记”等弱收口，不主动发付款卡或声称预约已成功。
- Validation: 确定性 Prompt 合同 125 passed；全仓 281 passed；指定真实日志 DeepSeek 隔离重放输出“预约登记＋10元预约金锁活动名额”，无门店卡、完整地址、发送或生产写入，耗时 12.177s。
- Rollback: production release `ai-paths-unified-20260906-153201-d085b01c`.

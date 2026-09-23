# Pending 自带内容发送

- 目标：非空 message_content 使用任务内容发送，成功消费不传 messages；空内容沿用 sop-messages。
- base：0bfb6678e2a9b7fd14f3b746361cf66c78783235；main 独立构建仓库。
- 生产及回滚基线：99038cbc；无 schema 或前端变更。
- 范围/ownership：SOP task service、确定性测试、SOP 合同。
- 边界：三项门禁、原发送结果语义保持；任务内容按 taskId 去重，旧路径按 msgId；恢复只补原消费请求。
- 已授权：修改提交上线；任务内容消费省略 messages，平台自动绑定行为沿用外部协议。
- 验证：合成确定性测试及线上只读健康/新流量审计。
- 状态：实施与验证中。

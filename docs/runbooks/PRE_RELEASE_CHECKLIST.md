# 生产发布前检查清单

## 代码来源

- [ ] `branch=main`，记录完整 commit SHA，`dirty=false`。
- [ ] reply、control、worker 均由同一 SHA 构建；记录 `service_role`，reply 的接口版本为 V3。
- [ ] 保存上一 release、服务状态和明确回滚步骤。

## 本次验证

- [ ] 当前任务已按改动范围完成最小隔离验证，结果写入任务文件。
- [ ] 验证未使用生产 token、真实客户数据、真实发送或生产写接口。
- [ ] 需要模型效果或真实链路验证的事项已单独批准并记录，不以历史报告替代。

## 现场核验

- [ ] 重新读取生产 release、commit、service unit、Nginx、数据库、worker/outbox 和队列状态。
- [ ] 核验 V3 reply、control、worker、消息送达回调、第三方 SOP 两段式链路和管理页。
- [ ] 核验退役 V1/V2 回复入口仍不可用；第三方协议路径中的版本号不误判为产品版本。
- [ ] 未批准的 shadow/关闭功能保持原状态。

任一项失败，停止发布或回滚到已记录 release。

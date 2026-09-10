# 生产发布前检查清单

## 代码来源

- [ ] `branch=main`，记录完整 commit SHA，`dirty=false`。
- [ ] reply、control、worker 均由同一 SHA 构建；记录 `service_role`，reply 的接口版本为 V3。
- [ ] 前端来自干净 `main`，与本次接口合同兼容；存在前后端耦合变更时同批发布并共同验收。
- [ ] 保存上一 release、服务状态和明确回滚步骤。
- [ ] 确认 `/opt/ai-paths/previous` 或等价回滚目标真实存在且与记录一致；仅有旧 release 目录不等于已验证回滚点。
- [ ] 如有数据库迁移，先备份并核对迁移 head、前后版本兼容性及代码回滚后的数据结构可用性。

## 本次验证

- [ ] 当前任务已按改动范围完成最小隔离验证，结果写入任务文件。
- [ ] 默认验证未使用生产 token、真实客户数据、真实发送或生产写接口；经明确批准的真实身份测试已证明只读、零发送、零生产写，并留存隔离审计。
- [ ] 需要模型效果或真实链路验证的事项已单独批准并记录，不以历史报告替代。
- [ ] 涉及回复、结构消息、记忆、BI、outbox 或接口性能时，按 `docs/standards/V3_REPLY_EVALUATION.md` 标明 L1–L4 覆盖；未执行模拟持久化和部署后 HTTP 验证时，不宣称“全链路通过”。
- [ ] 评审输入包含客户实际可见的文本和结构消息；硬安全、重复发送和关键业务推进目标由确定性断言验收，不只依赖 AI judge。

## 现场核验

- [ ] 重新读取生产 release、commit、service unit、Nginx、数据库、worker/outbox 和队列状态。
- [ ] 核对 reply、control、worker 的实际 commit、`dirty=false`、role 与 `/health`；核对前端 release 与本次接口兼容版本。
- [ ] 核验 V3 reply、control、worker、消息送达回调、第三方 SOP 两段式链路和管理页。
- [ ] 核验退役 V1/V2 回复入口仍不可用；第三方协议路径中的版本号不误判为产品版本。
- [ ] 未批准的 shadow/关闭功能保持原状态。
- [ ] 数据库 schema head 与本次迁移目标一致；代码回滚点不会因本次迁移立即失效。

任一项失败，停止发布或回滚到已记录 release。

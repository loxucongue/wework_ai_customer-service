# 生产发布前检查清单

本清单是发布前阻断项，不代表当前代码已发布。生产动态状态全部待现场核验。

## 代码与来源

- [ ] `branch=main`，不是临时分支或 detached HEAD。
- [ ] 记录完整 commit SHA，并确认该 SHA 已合入 `main`。
- [ ] `dirty=false`，构建输入与提交一致。
- [ ] control、reply、workers 来自同一个已验证 `main` SHA。
- [ ] 记录 `service_role` 和 `interface_version=v3`。
- [ ] 保存可恢复的上一 release 和回滚命令。

## 自动门禁

- [ ] 在干净环境完整执行 `python scripts/run_quality_gates.py`，返回 0。
- [ ] Python 编译、离线确定性测试和 V3 路由合同通过。
- [ ] 私有导入及异常债务未超过版本化基线。
- [ ] 隔离启动不再有 `strict xfail`；启动、`/health`、关闭均成功。
- [ ] 前端 TypeScript、ESLint 和生产构建通过冻结锁文件复现。
- [ ] 测试过程未使用生产 token、真实客户数据、真实发送接口或真实模型。

## 现场核验

- [ ] 重新读取生产 release、commit、service unit、Nginx 和 symlink；不得照抄文档旧值。
- [ ] 核验数据库后端、迁移状态、worker/outbox 单消费者和队列积压。
- [ ] 核验退役 V1/V2 公网回复入口仍返回 410；第三方协议路径中的 `v1` 不误判为产品 V1。
- [ ] 核验 V3 reply、共享控制面、worker、消息送达回调、SOP 策略回传和管理页。
- [ ] shadow 功能保持 shadow，除非另有经批准且可回滚的启用步骤。
- [ ] 需要真实模型效果或全链路在线验证的改动已单独执行并保留可审计结论；不得用自动生成的 400 条候选代替人工金标。

任一阻断项失败，停止发布或立即回滚到已记录 release。


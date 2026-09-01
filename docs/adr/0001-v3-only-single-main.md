# ADR-0001：V3-only 与单一 main

- status: accepted
- date: 2026-08-31
- code_consolidated: 2026-09-01

## 决策

客户回复产品只保留 V3；`main` 是唯一长期开发与生产分支。API、worker、frontend 可以拆进程，但最终必须从同一个干净的 main SHA 构建。旧产品 V1/V2 路由从应用代码和前端代理删除，公网退役地址由 Nginx 永久返回 410。

## 原因

生产目前由两个深度分叉的代码线拼接，旧 worktree 和功能分支容易把已修复规则覆盖回去。只关服务而不整合代码，会停止 SOP 等共享能力；整分支覆盖则会丢失另一条代码线的修复。

## 后果

- 收口采用选择性、按模块所有权的合并，不整分支覆盖。
- 生产进程可继续按 reply/control/worker 拆分，但不得再从不同分支或不同提交构建。
- 历史 schema/第三方协议版本保留兼容，不按字符串清除。
- 发布必须记录 commit、role、interface version 和回滚 release。

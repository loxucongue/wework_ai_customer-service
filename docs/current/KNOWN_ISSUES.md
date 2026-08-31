# 已知问题

- status: active
- owner: project
- last_verified: 2026-08-31 Asia/Shanghai
- source_of_truth: Git graph、worktree 状态、生产服务核验

## P0：生产代码线分裂

`origin/main@7af3065e` 有最新 SOP/MySQL/回调，但没有 V3 核心；`codex/v3-model-led-sales-brain@7c0cfc04` 有 V3 回复链但缺 main 后续修复。两者从 `be65e329` 分叉，不能互相覆盖或整分支粗暴合并。

## P0：未提交工作尚未保全

多个旧 worktree 含未提交文件，尤其 V3 worktree、旧 main、E 盘 detached 根目录、SOP candidate。清理 worktree 前必须生成 manifest，并将有效改动保全到明确提交或可恢复补丁；当前禁止删除这些 worktree。

## P1：部署配置漂移

仓库 `deploy/ai-paths.conf` 落后于生产有效 Nginx 配置。生产配置已收口旧路由，但 canonical 部署清单尚未回写完整，不能用旧仓库文件覆盖服务器。

## P1：策略数据消费者归属错误

`service-rule-data` outbox 当前由 V3 reply sidecar 启动，现场曾观察到 `sent=1903`、`dead=15`。统一运行角色时必须把它连同必要环境变量迁到 workers，并确认只有一个消费者；否则清理 sidecar 装配会悄悄停止策略数据回传。

## P1：旧命名与产品版本混杂

V3 在线链仍 import 若干 `v2_*` 内部模块。需要先无行为重命名和回归，再删除真正的 V2 service；不能字符串批量删除。

## P1：数据与产物

- 已跟踪的 `long_text_*.txt` 含真实客户会话标识，需从当前树移除并评估 Git 历史泄露。
- 多个 worktree 有大量历史部署包、报告和缓存；确认未被线上/未提交工作引用后再归档清理。
- 第三方 SOP 历史文档仍有旧 `10/20/30` 口径，必须由 V3 合同替代。
- 前端 `validate` 聚合脚本在 Windows 使用不兼容的并行命令格式；当前工作区依赖安装也未生成 `node_modules`，因此本轮只能记录校验阻塞，不能把大量缺模块报错当作代码回归。

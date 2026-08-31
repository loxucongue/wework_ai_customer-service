# 生产状态

- status: observed
- owner: operations
- last_verified: 2026-08-31 Asia/Shanghai
- source_of_truth: 服务器 `47.252.81.104` 现场核验

## Release

| 角色 | 服务 | 当前 release / commit |
|---|---|---|
| shared API | `ai-paths.service` | `ai-paths-server-20260831-sop-unopened-ruledata-7af3065e` |
| worker | `ai-paths-workers.service` | 与 shared API 代码线一致 |
| V3 reply | `ai-paths-v3.service` | `ai-paths-v3-server-20260826-reply-evidence-7c0cfc04` |
| frontend | `ai-paths-frontend.service` | active；待统一 release manifest |

这不是目标状态：生产由 main 与 V3 两个不同 SHA 拼接运行。任何发布前必须重新现场核验，不得照抄本页。

## 已执行的 V1/V2 containment

- 旧公开回复入口已由 Nginx 返回 410，包括两个 Next 前端旧代理入口。
- `ai-paths-refactor.service` 与 `ai-paths-backend.service` 已停止并禁用。
- `ai-paths-refactor.service` 已 mask；`/opt/ai-paths-refactor` 已删除，路径/大小清单和 unit 备份保存在服务器 root 私有目录。
- V3 health 正常；共享 API、worker、frontend 保持 active。
- Nginx 修改前配置备份：`/etc/nginx/conf.d/ai-paths.conf.pre-v3-only-20260831`。

## 数据与容量

- 现场观察根 `.env` 仍使用 SQLite；MySQL cutover 状态在任何修改前必须重新确认。
- 主服务与 V3 历史 release 已各收敛为 3 个（当前 + 两个最近回滚版本）。
- 清理后服务器根盘约 73% 使用率；删除的 release 目录只可从 Git/构建重新生成，服务器保留了私有 manifest。

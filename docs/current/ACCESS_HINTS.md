# 访问提示

本页只记录非密钥的协作提示，帮助新窗口快速找到服务器入口。这里不是生产状态快照；每次涉及发布、线上数据、服务健康或 release 时仍必须现场核验。

## AI Paths 服务器 SSH

- 用途：只读核验、发布前检查、从服务器本地环境执行受控同步脚本。
- Host：`47.252.81.104`
- User：`root`
- Key path：`C:\Users\24159\.ssh\ai-paths-aliyun.pem`
- 示例：

```powershell
ssh -i "C:\Users\24159\.ssh\ai-paths-aliyun.pem" root@47.252.81.104
```

## 约束

- 不把私钥内容、token、密码、完整 `.env` 或客户原文写入 Git。
- 服务器 `/opt/ai-paths/.env` 只能在服务器本地读取或用于远程命令环境，不拷贝进仓库。
- 文档中的 IP 和路径只是访问提示；使用前必须重新确认连通性、权限和目标环境。

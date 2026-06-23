# PowerShell 中文编码问题记录

## 现象

在 Codex 桌面环境里直接用 PowerShell here-string、管道输出或 `Get-Content` 查看中文脚本/日志时，中文有时会显示成 `????` 或乱码。

## 结论

这类问题目前主要是 **PowerShell 输入/显示编码问题**，不是源文件一定被写坏了。

已经确认过的情况：

- Python 用 `encoding="utf-8"` 读取文件时，文件内容正常。
- PowerShell 直接显示同一文件时，可能出现 `????`。
- 批量测试脚本如果通过 PowerShell here-string 直接把中文传给 Python，也可能把中文参数腐蚀成问号。

## 当前处理原则

1. **不要** 用 PowerShell here-string 直接向 Python 注入中文测试样本。
2. 中文测试数据、提示词、日志导出，统一走：
   - UTF-8 文件落盘
   - Python `-X utf8`
   - `Path(...).read_text/write_text(encoding="utf-8")`
3. 判断文件是否真的损坏时，以 **Python UTF-8 读取结果** 为准，不以 PowerShell 屏幕显示为准。

## 推荐做法

- 读文件：
  - `python -X utf8` + `Path.read_text(encoding="utf-8")`
- 写文件：
  - `Path.write_text(..., encoding="utf-8")`
- 跑中文批量测试：
  - 先把输入写成 UTF-8 JSON / TXT / PY，再执行脚本

## 备注

如果后续还出现“接口/知识库检索不到，日志里 query 变成 `????`”的情况，优先检查：

1. 测试脚本是不是通过 PowerShell 直接内联了中文；
2. 临时 JSON 是否确实按 UTF-8 落盘；
3. Python 启动是否带 `-X utf8`。

### 2026-06-16 复现记录

本轮在本地做门店接口烟测时再次复现：

- 同样的 Python 逻辑，直接把中文 query 放进 PowerShell here-string 后，会在 Python 里收到 `????`；
- 改成 `\\uXXXX` Unicode 转义、UTF-8 文件脚本或 JSON 落盘后，query 恢复正常；
- 因此 **PowerShell here-string 不能再作为中文测试输入通道**，后续所有中文批量测试统一走 UTF-8 文件或 Unicode 转义。

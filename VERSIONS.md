# Sun 版本历史

用户说「生成版本」时：在本文件**顶部**追加一条，同步改 `pyproject.toml` 与 `src/harness/__init__.py` 的版本号。变更说明尽量简洁。

---

## 0.2.0 — 2026-08-04

- 新增 `fetch_url` / `search_web`；同 URL 约 15 分钟缓存；先搜再抓
- 删除类命令（rm/del/Remove-Item 等）一律需确认
- 可编辑人格 `PERSONA.md`（`sun persona`）；每轮任务重载
- 建立本版本记录约定

## 0.1.0 — （基线）

- Stage 1/2 内核：工具循环、编辑/验证、REPL、上下文压缩、用量等

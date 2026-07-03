# Roadmap

## Stage 1 — Linux CLI closed loop  ← current

**Definition of Done:** the agent passes these four acceptance cases:

| # | Case | Verifies |
|---|------|----------|
| 1 | "统计当前目录有多少个 .py 文件" | single-tool closed loop |
| 2 | "找出占用磁盘最大的 3 个文件并删掉最大的那个" | multi-turn + dangerous-op confirmation |
| 3 | "写一个快排 python 脚本并运行验证输出正确" | write → run → observe → self-verify |
| 4 | "这个报错怎么修:`<traceback>`" | read → analyze → edit → rerun |

### Milestones
- [x] **M0 骨架** — uv project, config, package structure, deps
- [x] **M1 单工具闭环** — run_shell + function calling, case 1 ✅ (agent 自跑 `find|wc -l` 得 12)
- [x] **M2 多轮循环** — AgentLoop + finish + max-turns, case 3 ✅ (write→run→verify 快排)
- [x] **M3 全工具 + 安全** — read/write_file, dangerous-op confirm, case 2 ✅ (删最大文件，删前弹确认)
- [x] **M4 打磨** — retry/backoff, CI, CONTRIBUTING, ruff format, pytest ✅
- [x] **M5 分发/CLI 产品化** — 子命令(model/config/update/remove/help/version) + 全局配置(~/.config/sun/config.toml) + install.sh(GitHub raw 一行安装) ✅

### 已验证 (with DeepSeek deepseek-v4-flash)
- Case 1 统计 .py 文件数 → 正确得 12
- Case 2 删最大文件 → 正确识别并删除，删除前触发确认门
- Case 3 写快排并自验证 → write→run→observe "排序正确" 闭环

### 踩坑记录 (供开源者参考)
1. **安全规则误报**：初版 `>\s*/(...dev...)` 把 `2>/dev/null`（stderr 重定向到空设备）
   误判为"写入系统路径"。修正为 `(?<![0-9&])>>?\s*/(...|dev/(?!null))`——排除 fd
   前缀（`2>`）和 `/dev/null` sink，只拦真正的写重定向。
2. **确认在非 TTY 下卡死**：`rich.Confirm.ask` 在管道/无 TTY 环境读 stdin 会永久阻塞。
   修正为 `sys.stdin.isatty()` 检测，非交互环境 fail-safe 自动拒绝危险操作。

### Out of scope (Stage 1)
Multi-agent · web/browser · persistent DB · provider abstraction · GUI ·
streaming · context compression (in-memory only for now).

## Stage 2 — candidate directions
- Provider abstraction + fallback chain
- Context compression (rolling summary, tool-output offloading to disk)
- Persistent sessions (SQLite) + resume
- More tools: HTTP client, structured file edit (patch)
- Observability: structured trace log

## Stage 3+ — later
- Sub-agent delegation, parallel workflows
- Sandbox hardening (Docker / Firecracker)
- Plugin system / MCP tool ingestion

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

## Stage 2 — 升级为"编程 agent"（能真正写代码）

从单轮任务型 → 编程助手（对标 Claude Code / Codex / Aider）。按 ROI 排序，
每个子阶段都能独立跑通：

### ① 编辑地基（最优先，质变的一步）
没有精准编辑就改不了代码。当前只有 `write_file`（整文件覆盖），必须补：
- [ ] `edit_file` — 字符串替换（找旧文本→换新文本，不动其余），对标 Claude Code 的 Edit
- [ ] `read_file` 分页 — offset+limit 只读大文件的某段，带行号
- [ ] `search_files` — 按内容正则搜代码，定位函数/变量在哪（grep）
- [ ] `find_files` — 按文件名 glob 找文件（`*.py`）
- [ ] 列目录树 — 看项目结构
- [ ] 强化 system prompt：先读后改、改小块不整写、改完必验证

### ② 验证闭环（让它"改对"而不只是"改了"）
- [ ] 跑测试（pytest/npm test），失败信息喂回让它自修 → TDD 循环：改→跑→看红→再改→绿
- [ ] 跑 linter/类型检查（ruff/mypy/eslint），自动发现问题
- [ ] 写文件后自动语法检查（`python -m py_compile` 等）

### ③ 安全与可控（改代码是高风险操作）
- [ ] 每次编辑先展示 diff（可选确认）
- [ ] git 集成：改前自动 checkpoint，改错能回滚；自动生成 commit
- [ ] 限制只在项目目录内操作，防误改项目外文件

### ④ 上下文管理（改大项目不爆窗口，Stage 2 的硬骨头）
- [ ] 上下文压缩：多轮编辑历史滚动摘要
- [ ] 大文件/大输出裁剪：读整文件、测试输出几千行 → 只留关键部分
- [ ] 相关文件召回：按任务只把相关文件读进上下文，不全塞

### 其他候选方向（非编程专属）
- [ ] Provider 抽象 + fallback 链
- [ ] 持久化会话（SQLite）+ 断点续跑（跨任务记忆）
- [ ] 计划模式：大任务先列步骤给用户批准再动手（Davis 喜欢的"待确认→进行中"流程）
- [ ] 可观测：结构化 trace log
- [ ] 流式输出

> **建议起点**：先做 ①，Sun 就能从"跑命令"进化到"能改代码"，工作量可控
> （加几个工具 + 强化 prompt），ROI 最高。

## Stage 3+ — later
- Sub-agent delegation, parallel workflows
- Sandbox hardening (Docker / Firecracker)
- Plugin system / MCP tool ingestion

# Roadmap

## 产品北极星（Davis）

自家用终端编程 agent（不必做成 Cursor 产品）。核心闭环要跑通：

> **需求（可补充澄清）→ 拆分任务 → 不同模型执行不同子任务 → 编码/改码 → 测试验证自修 → 记忆可续**

| 能力 | 含义 | 落在哪 | 现状 |
|------|------|--------|------|
| **读/搜/精改** | 定位代码并小块修改 | Stage 2① | 仅有整文件 write |
| **跑测自修** | 改→测→红→再改→绿 | Stage 2② | 能 write→run，无测/ lint 工具化 |
| **需求补充** | 含糊需求先问清再动手 | Stage 2⑤（新增） | 无 |
| **任务拆分** | 计划 + todo，按步执行 | Stage 2④⑤ | 无 |
| **长任务** | 批准、断点、预算、插话 | Stage 2⑤ | 仅 max_turns |
| **会话/项目记忆** | 跨轮不忘；跨天/跨进程可续 | Stage 2④ → Stage 3③ | 仅任务内 Context |
| **多模型分责** | 规划/编码/审查等用不同模型 | Stage 2.5 薄路由 → Stage 3①② | 单 model |
| **外接工具** | MCP / 插件 | Stage 3④ | 无（ZCode 侧可另用） |

学习二阶段（吃透 Stage 1 内核）完成前，**产品大项不抢跑**。

### 能力对照（你要的是否已覆盖）

| 你要的 | 计划里有没有 | 阶段 | 缺口/动作 |
|--------|--------------|------|-----------|
| 读/搜/精改代码 | ✅ 有 | 2① | — |
| 跑测自修闭环 | ✅ 有 | 2② | 补「验收标准进计划、未绿不 finish」 |
| 长任务拆步 | ✅ 有 | 2④ todo + 2⑤ 计划模式 | — |
| **按需求补充/澄清** | ❌ 原先弱 | **2⑤ 补强** | 增加「需求澄清门」 |
| 会话记忆 | ✅ 有 | 2④ REPL | — |
| 项目记忆 | ✅ 有 | 3③ | — |
| **不同模型执行不同任务** | ✅ 有但偏后 | **2.5 薄路由 + 3①②** | 2.5 先做「按阶段换模型」；完整多 agent 放 3 |
| 测试验证闭环 | ✅ 有 | 2② + 2⑤ 验收句 | 与计划模式绑死 |
| 外接工具 MCP | ✅ 有 | 3④ | 可选 |

---

## Stage 1 — Linux CLI closed loop  ← current (产品已完成；学习中)

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
- [ ] **未绿不 finish**：system prompt + loop 约定——计划中的验收命令未通过不得调用 `finish`
- [ ] 可选：`run_tests` 专用工具（封装常用测试命令，输出结构化失败摘要）

### ③ 安全与可控（改代码是高风险操作）
- [ ] 每次编辑先展示 diff（可选确认）
- [ ] git 集成：改前自动 checkpoint，改错能回滚；自动生成 commit
- [ ] 限制只在项目目录内操作，防误改项目外文件

### ④ 记忆与上下文（改大项目不爆窗口；记忆管理的第一层）
- [ ] **REPL 会话记忆（内存）**：交互模式跨 `sun>` 行复用同一 Context（修「问完就忘」）
- [ ] 上下文压缩：多轮编辑历史滚动摘要
- [ ] 大文件/大输出裁剪：读整文件、测试输出几千行 → 只留关键部分
- [ ] 相关文件召回：按任务只把相关文件读进上下文，不全塞
- [ ] 工作记忆工具：`todo_write` / 任务清单，长任务中途可勾进度（对标 Cursor/Claude Code）

### ⑤ 需求 → 拆步 → 长任务执行（你最关心的主闭环，单模型先跑通）
- [ ] **需求澄清门**：目标含糊或缺约束时，先提问补充（范围/验收/技术栈），得到确认再进计划；支持用户中途追加需求并改计划
- [ ] **计划模式**：大任务先列步骤 + **每步验收标准** → 用户批准 → 再动手（待确认→进行中）
- [ ] 按计划驱动 `todo_write`：拆分后的子任务与计划步骤一一对应，完成勾选
- [ ] 任务级断点：中断后可从最近 checkpoint 续跑（不必重头）
- [ ] 预算门：max_turns / 预估 token·费用·墙钟上限，超限优雅 stop + 摘要
- [ ] 人机插话：执行中可 pause / 改计划 / 补充约束
- [ ] 长命令：后台 shell + 轮询日志（避免把 loop 卡死在一次性超长命令）

### ⑥ 可观测与体验（编码/长任务共用）
- [ ] 结构化 trace log（每轮 think / tool / 耗时 / token）
- [ ] 流式输出（思考与工具进度边出边显）
- [ ] 费用/token 统计（方便选模型与控预算）

### Stage 2 建议顺序（自家用）
1. **① 编辑地基** → 能精改  
2. **② 验证闭环** → 改对  
3. **⑤ 需求澄清 + 计划/拆步** → 主闭环成形（仍单模型）  
4. **④ REPL 会话记忆 + todo** → 体感不「失忆」  
5. ③⑥ 按需穿插（git checkpoint 很值，流式可后做）

### Stage 2 验收（单模型主闭环 DoD）
用一条模糊需求跑通，例如：

> 「给当前目录加一个统计 .py 行数的小工具，要能测。」

期望路径：**追问补充 → 出计划+验收句 → 批准 → 读/搜/改 → 跑测红→自修→绿 → finish**。  
（此阶段允许全程一个模型；多模型换脑见 Stage 2.5 / 3。）

> **建议起点**：先做 ①，Sun 就能从"跑命令"进化到"能改代码"。

---

## Stage 2.5 — 薄多模型路由（先换脑，不上多 agent）

在 Stage 2 主闭环已通、但还不想上子 agent 时做。满足「**不同阶段用不同模型**」的最小集：

- [ ] 配置多槽位：如 `models.planner` / `models.coder` / `models.reviewer`（仍走同一 OpenAI-compatible 客户端）
- [ ] **按计划步骤切模型**：规划步用 planner、编码步用 coder、审/总结用 reviewer（规则表即可，不必分类器）
- [ ] 单步失败可 fallback 到备用模型
- [ ] trace 里记录「本步用了哪个 model」（接 2⑥）

**不做**：独立子 agent、并行、工具白名单隔离（留给 Stage 3②）。

### Stage 2.5 验收
同一任务 trace 中至少出现 **两种 model id**，且规划输出与编码改动分别来自配置的槽位。

---

## Stage 3 — 多 agent · 持久记忆 · 外接工具

在 Stage 2（+ 建议 2.5）稳定后开做。完整「多模型分责 + 记忆深化」。

### ① 多模型路由（加厚 2.5）
- [ ] Provider 抽象 + fallback 链（跨厂商/多 endpoint）
- [ ] **按角色绑模型**（与 2.5 对齐并增强）：planner / coder / reviewer / cheap
- [ ] 子任务级覆盖：某一步可指定 `model=`，不必整段会话锁死一个模型
- [ ] 路由策略可配置（规则表或轻量分类器），默认有一条稳妥预设

### ② 多 agent 协作（不同子任务不同职责）
- [ ] Sub-agent 委托：主 agent 拆任务 → 子 agent 带独立 Context + 指定模型执行 → 结果汇总
- [ ] 并行工作流：互不依赖的子任务并行（需隔离工作区/文件锁策略）
- [ ] 角色提示词与工具白名单（审查员只读、编码员可写等）
- [ ] 子任务完成后主 agent 做集成验证（再跑总测）

### ③ 记忆管理（分层，避免全塞进 Context）
- [ ] **会话持久化（SQLite）** + 跨进程恢复（关掉终端还能续）
- [ ] **项目记忆**：仓库级笔记 / 约定 / 已做决策（如 `MEMORY.md` 或 DB），新会话可加载
- [ ] **情景摘要**：长会话周期性写入摘要，细节可回查
- [ ] 记忆读写工具：agent 可主动 `remember` / `recall`，而非只靠隐性 Context
- [ ]（可选）代码库语义检索（embedding / 简易索引）增强「相关文件召回」

### ④ 扩展与加固
- [ ] Plugin / MCP tool ingestion（外接工具生态）
- [ ] Sandbox hardening（Docker / Firecracker）
- [ ] 多工作区 / monorepo 根目录绑定

### Stage 3 验收（多模型主闭环 DoD）
> 模糊需求 → 澄清 → 拆步 → **planner 模型出计划** → **coder 模型改代码** → 跑测自修 → **reviewer 模型看一眼** → 项目记忆留下约定 → finish。

### 刻意靠后（有需要再开）
- Web/browser 自动化 · GUI · 云端多租户

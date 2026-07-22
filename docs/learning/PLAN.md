# 学习计划：把 Python 项目做成可安装的 CLI 工具

> **目标**：以 Sun Agent Harness 为活教材，吃透"如何编写一个可通过 CLI
> 安装 / 运行 / 移除的 Python 工具"。学完能自己从零做一个类似的工具。
>
> **节奏规则**（Davis 约定）：
> - 一小节一小节来，随时开始、随时结束。
> - 每节含：讲原理 → 读真实代码 → 动手实验 → **验证卡点**。
> - **验证不通过不得进入下一节。**
> - 每节完成后更新本文件的「状态」和「验证结果」。

---

## 进度总览

| # | 小节 | 状态 | 验证 |
|---|------|------|------|
| 1 | pyproject.toml 与项目骨架 | ✅ 已掌握 | ✅ |
| 2 | 入口函数：`sun` 命令如何跑起来 | ✅ 已掌握 | ✅ |
| 3 | typer 子命令与参数路由 | ✅ 已掌握 | ✅ |
| 4 | 配置管理：key 存哪、优先级 | ✅ 已掌握 | ✅ |
| 5 | 打包与安装机制（uv tool install/uninstall） | ✅ 已掌握 | ✅ |
| 6 | 分发：install.sh 一行安装 | ✅ 已掌握 | ✅ |
| 7 | 毕业项目：从零做一个 mini CLI 工具 | ✅ 已掌握 | ✅ |

状态图例：⬜ 未开始 · 🔵 进行中 · ✅ 已掌握 · ⏸ 暂停

---

## 各小节详细内容与验证标准

### 小节 1 — pyproject.toml 与项目骨架
- **学什么**：包名/命令名/导入名的区别；`[project]`/`[project.scripts]`/
  `[build-system]`/`[tool.*]` 各段作用；`src/` 布局为什么这么摆。
- **读代码**：`pyproject.toml`、项目目录树。
- **动手**：加一个命令别名（如 `sunny`）指向同一入口，重装后验证它能用。
- **✅ 验证卡点**：能用自己的话说清「敲 `sun` 为什么能跑代码」，并能独立
  在 `[project.scripts]` 里加一个新命令别名并让它生效。

### 小节 2 — 入口函数：`sun` 命令如何跑起来
- **学什么**：console_scripts 生成的可执行文件干了啥；`main()` 入口；
  `sys.argv` 预处理（Sun 的自由文本路由）。
- **读代码**：`src/harness/__main__.py` 的 `main()` 和 `if __name__`。
- **动手**：在 `main()` 里加一句打印 argv，观察 `sun "hi"` 被改写成 `sun run "hi"`。
- **✅ 验证卡点**：能画出「敲 sun → sun.exe → main() → typer」的调用链，并解释
  argv 预处理解决了什么问题。

### 小节 3 — typer 子命令与参数路由
- **学什么**：`typer.Typer()`、`@app.command()`、`@app.callback()`；子命令
  vs 自由文本如何分流；`typer.Option`/`Argument`。
- **读代码**：`__main__.py` 里的 model/config/version/update/remove 命令。
- **动手**：加一个新子命令 `sun ping`（打印 "pong"），重装后验证。
- **✅ 验证卡点**：能独立新增一个子命令并跑通；能说清 `sun model`（子命令）
  和 `sun "任务"`（自由文本）为什么走不同路径。

### 小节 4 — 配置管理：key 存哪、优先级
- **学什么**：环境变量 / 项目 .env / 全局 config.toml 三层来源；pydantic-settings
  的 `settings_customise_sources` 优先级；`sun model` 如何写配置。
- **读代码**：`config.py`、`config_writer.py`。
- **动手**：用三种方式各设一次 model 名，验证「env > .env > 全局」的覆盖顺序。
- **✅ 验证卡点**：能预测「同时存在 env 和全局配置时，最终用哪个」并实测验证正确。

### 小节 5 — 打包与安装机制
- **学什么**：wheel 是什么；`uv tool install` 把命令装到哪（~/.local/bin）；
  `--force` 重装；`uv tool uninstall <包名>`；开发期 `uv run` vs 全局安装的区别。
- **读代码**：无（命令行为主），观察安装产物路径。
- **动手**：完整走一遍 卸载 → 从本地装 → 验证 → 卸载 → 从 GitHub 装。
- **✅ 验证卡点**：能说清 `uv run sun` 和全局 `sun` 用的是不是同一份代码；
  能独立完成一次干净的卸载+重装并验证版本。

### 小节 6 — 分发：install.sh 一行安装
- **学什么**：`curl | bash` 原理；install.sh 的检测/安装/配 PATH 逻辑；
  `set -euo pipefail`；GitHub raw 托管。
- **读代码**：`install/install.sh`。
- **动手**：逐行读脚本，`bash -n` 语法检查，讲清每一步在干嘛。
- **✅ 验证卡点**：能逐段解释 install.sh，并说清它和「从 GitHub 装 sun」是两件事。

### 小节 7 — 毕业项目（综合验证）
- **做什么**：从零建一个 mini CLI 工具（如 `note`：`note add/list/remove`，
  配置存全局 toml，可 `uv tool install`），不抄 Sun 代码、凭理解自己写。
- **✅ 验证卡点**：`note` 能安装、加子命令、跑、卸载全通 —— 即毕业。

---

## 学习日志（每节完成后追加）

<!-- 格式：日期 · 小节 · 结论 · 验证是否通过 -->
- 2026-07-07 · 小节 1 · 掌握 pyproject.toml 骨架 + [project.scripts] 机制 · ✅ 验证通过（链路讲全、别名写法正确）
- 2026-07-08 · 小节 2 · 掌握 main() 入口 + argv 预处理机制 · ✅ 验证通过（调用链四步正确、理解预处理解决"省掉 run"问题）
- 2026-07-08 · 小节 3 · 掌握 typer 子命令注册/路由 + Argument vs Option + callback + 自由文本分流 · ✅ 验证通过（两路径区别正确、新增子命令两步骤正确）
- 2026-07-09 · 小节 4 · 掌握配置四层优先级（env > .env > 全局 toml > 默认）+ settings_customise_sources 元组顺序定优先级 + env_prefix 前缀 + 自定义 _GlobalTomlSource + 读写分离（tomllib 只读不写） · ✅ 验证通过（实测逐层覆盖，同时存在 env/.env 时预测 env 赢且实测正确）
- 2026-07-12 · 小节 5 · 掌握打包安装机制三大认知：① 打包=把 src/+entry_points+元数据凝固成快照；② 命令名在 entry_points.txt（`sun = harness.__main__:main`），不在代码里，uv 据此生成 launcher；③ uv tool install 装到隔离 venv（Windows 在 %APPDATA%\uv\tools\<包>\），不污染系统 Python，卸载连根端掉不留残。三种运行方式实测对比：`uv run sun` 读 src/ 实时源码、本地装/GitHub 装读隔离 venv 快照——改源码版本号后 uv run 反映、全局 sun 不变，亲手证明"装的是快照非链接"。GitHub 装带 @<commit> 冻结语义 · ✅ 验证通过（答对"两入口读不同代码"、卸载命令正确、三实验全通）
- 2026-07-13 · 小节 6 · 掌握 install.sh 一行安装分发：① `curl -fsSL <url> | bash` = curl 下载脚本文本(不落盘) + 管道喂给 bash 执行；raw.githubusercontent.com 给原始文本正好能喂 bash；② `set -euo pipefail` 三件套（出错即停/未定义变量报错/管道任一环失败即失败）是安全带；③ 四步流程：确保 uv → uv tool install --force git+https → 配 ~/.local/bin PATH（否则 command not found）→ 引导 sun model；④ install.sh 是包在「uv tool install」外的一层壳，负责前置条件，二者不是一回事。`bash -n` = 语法体检 · ✅ 验证通过（bash -n 语法过、三段核心逻辑复述全对）
- 2026-07-15 · 小节 7（毕业项目）· 从零全手敲做出 mini CLI「note」（放 D:\phpstudy_pro\WWW\notecli），把前 6 节全串起来实战：手写 pyproject（[project]/[project.scripts]/[build-system]）；踩 hatchling 打包坑并自解（包名 note-test 与 src/notecli 不同名，需补 [tool.hatch.build.targets.wheel] packages=["src/notecli"]）；独立写 add/list/remove 三子命令，会用 @app.command("list") 避开内置名、int 参数自动转换、写回用 "w" 覆盖、按编号删（index-1 下标换算）、边界校验（删空列表不崩）；uv tool install --force . 装、uv tool uninstall note-test 卸全走通，亲历"包名≠命令名≠导入名"三处实战 · ✅ 毕业通过（全流程 add→list→remove→越界→卸载 全绿，代码全部本人手敲、多处自主发挥非照抄）
- 🎓 全部 7 小节完成，CLI 工具化学习计划毕业。

---

## 🎓 一阶段收尾（2026-07-15）

**状态**：已毕业 · 7/7 验证通过  
**速查表**：[cheatsheet_phase1_cli.md](./cheatsheet_phase1_cli.md)（遗忘时先翻这页）  
**毕业证据**：`D:\phpstudy_pro\WWW\notecli`（装 / 跑 / 卸全通）

### 这一阶段真正拿到的能力

能从零交付一个可分发的 Python CLI：写 `pyproject` → 挂入口 → typer 子命令 → 可选配置层 → `uv tool install` / uninstall →（可选）`install.sh` 一行装。

### 心智模型（一句话版）

> **命令名**写在 entry_points；**装上的是快照**不是源码链接；**配置有优先级**；**install.sh 只是 uv 外面的壳**。

### 和产品路线的关系

学习一阶段 ≈ 吃透了产品 [roadmap Stage 1](../roadmap.md) 的 **M5 分发/CLI 产品化**。  
产品 Stage 1（Linux CLI 闭环 M0–M5）本身也已完成。

### 下一步

| 方向 | 状态 | 入口 |
|------|------|------|
| **B · 学习二阶段（Agent 内核）** | 🔵 已开计划 · 一周吃透 Stage 1 | [PLAN_phase2_agent.md](./PLAN_phase2_agent.md) |
| **A · 产品 Stage 2** | ⬜ 毕业后开 · 编码+需求澄清+拆步+测通（单模型） | [roadmap.md](../roadmap.md) Stage 2 |
| **A2 · 产品 Stage 2.5** | ⬜ Stage 2 主闭环后 · 薄多模型按阶段换脑 | [roadmap.md](../roadmap.md) Stage 2.5 |
| **C · 产品 Stage 3** | ⬜ · 多 agent / 持久记忆 / MCP | [roadmap.md](../roadmap.md) Stage 3 |

产品北极星与能力对照表见 [roadmap.md 顶部](../roadmap.md)。

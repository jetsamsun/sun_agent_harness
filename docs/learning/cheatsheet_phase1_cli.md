# 一阶段速查表 · Python CLI 工具化

> 目标：从零做出「可安装 / 可运行 / 可卸载」的 Python CLI。
> 活教材：Sun Agent Harness + 毕业项目 `notecli`。
> 判断标准：能独立复现 `note` 那条链路 → 一阶段通。

---

## 1️⃣ 三名分离（最容易混）

| 名字 | 写在哪 | 例子（Sun） | 例子（note） |
|------|--------|-------------|--------------|
| **包名 / 分发名** | `[project].name` | `sun-harness` | `note-test` |
| **命令名** | `[project.scripts]` 左边 | `sun` | `note` |
| **导入名** | `src/` 下目录 + entry 右边 | `harness` | `notecli` |

```toml
[project.scripts]
sun = "harness.__main__:main"   # 命令 = "模块路径:函数"
```

> 💡 卸载用**包名**：`uv tool uninstall sun-harness`，不是 `uninstall sun`。

包名 ≠ 目录名时，hatchling 要显式声明：

```toml
[tool.hatch.build.targets.wheel]
packages = ["src/harness"]   # 或 ["src/notecli"]
```

---

## 2️⃣ 敲命令后发生了什么

```
终端敲 sun
  → PATH 上的 launcher（Windows: sun.exe）
    → 调 entry_points 里的 harness.__main__:main
      → main() 预处理 argv（自由文本 → 插入 run）
        → typer app() 路由到子命令
```

自由文本分流（Sun 的技巧）：

```
sun model          → 已是子命令，原样进 typer
sun "统计 .py"     → main() 改写成 sun run "统计 .py"
```

三种运行方式读的不是同一份代码：

| 方式 | 读什么 |
|------|--------|
| `uv run sun` | 仓库 `src/` 实时源码 |
| `uv tool install .` | 隔离 venv 里的**快照** |
| `uv tool install git+https://...` | 远程某 commit 的快照 |

---

## 3️⃣ typer 子命令速记

```python
app = typer.Typer()

@app.command()
def add(text: str): ...          # 位置参数 → Argument

@app.command("list")             # 避开 Python 内置名 list
def list_notes(): ...

@app.command()
def remove(index: int): ...      # int 自动转换

def main():
    app()
```

- `@app.callback()`：无子命令时的默认行为 / 全局选项
- `typer.Option("--flag")`：可选命名参数

---

## 4️⃣ 配置优先级（Sun）

高 → 低：

```
环境变量 (SUN_*)  >  项目 .env  >  全局 ~/.config/sun/config.toml  >  代码默认值
```

实现要点：
- `pydantic-settings` 的 `settings_customise_sources` **元组顺序 = 优先级**（先出现的赢）
- `env_prefix` 控制环境变量前缀
- 读用 `tomllib`，写用自己的 writer（读写分离）

---

## 5️⃣ 打包 / 安装 / 卸载

```bash
# 开发期（读源码）
uv run sun --help

# 装到全局工具（隔离 venv）
uv tool install --force .

# 从 GitHub 装（可钉 commit）
uv tool install --force git+https://github.com/jetsamsun/sun_agent_harness.git

# 卸（用包名）
uv tool uninstall sun-harness
```

- wheel = 源码 + entry_points + 元数据的凝固快照
- Windows 工具 venv 大致在 `%APPDATA%\uv\tools\<包名>\`
- launcher 在 PATH 上的 bin 目录；卸包装会连根端掉

---

## 6️⃣ 一行安装（install.sh）

```bash
curl -fsSL <raw-url>/install.sh | bash
```

= curl 拉脚本文本（不落盘）→ 管道喂给 bash。

脚本四步：确保有 uv → `uv tool install --force git+...` → 配 PATH → 引导 `sun model`。

`set -euo pipefail`：出错即停 / 未定义变量报错 / 管道任一环失败即失败。

> 💡 `install.sh` 是包在 `uv tool install` 外的壳（前置条件），二者不是一回事。
> `bash -n install.sh` = 只做语法体检，不执行。

---

## 7️⃣ 从零 checklist（复现 note 级工具）

1. `src/<导入名>/__main__.py` + `main()` + typer 子命令  
2. `pyproject.toml`：`[project]` / `[project.scripts]` / `[build-system]` / hatch `packages`  
3. `uv tool install --force .` → 命令能跑  
4. 增删改子命令 → 重装验证  
5. `uv tool uninstall <包名>` → 命令消失  

---

## 🎯 毕业自测（口述）

- [ ] 包名 / 命令名 / 导入名各举一例，卸载命令用哪个？
- [ ] 画出：敲 `sun` → launcher → `main()` → typer
- [ ] 为什么 `sun "hi"` 和 `sun model` 走不同路径？
- [ ] 同时有 env 和全局 toml 时，用哪个？
- [ ] `uv run sun` 和全局 `sun` 改源码后谁会变？
- [ ] `install.sh` 和 `uv tool install git+...` 各负责什么？

> 全答得出 → 一阶段毕业。答不出 → 回对应小节日志，别赶。

---

## 📦 证据

| 项 | 位置 |
|----|------|
| 学习计划 + 日志 | `docs/learning/PLAN.md` |
| 毕业项目 | `D:\phpstudy_pro\WWW\notecli` |
| 活教材入口 | `pyproject.toml`、`src/harness/__main__.py`、`install/install.sh` |

# BioRender GUI Agent 环境手册

## 1. 必需环境

| 项目 | 要求 | 用途 |
|---|---|---|
| 操作系统 | 当前主要在 Windows 10/11 验证 | 运行 Python、Playwright 和可见浏览器 |
| Python | 3.11 或更高版本 | 运行 Agent、CLI、FastAPI 和测试 |
| Playwright | 1.48–1.x | 控制 Chromium 浏览器 |
| Chromium | 由 Playwright 安装 | 执行真实鼠标和键盘操作 |
| SQLite | Python 内置 | 保存 Figure、动作、元素和恢复状态 |
| BioRender Account | 用户自己的有效账号 | 人工登录 BioRender 编辑器 |
| 网络 | 能正常访问 BioRender | Live Mode 使用 |

建议使用 8 GB 以上内存、至少 2 GB 可用磁盘空间和稳定网络。逐动作截图可能占用较多空间。

## 2. 安全要求

开始 Live Mode 前需要：

- 用户亲自完成账号、密码和 MFA 登录；
- 准备一个可丢弃的空白 BioRender Figure；
- 复制完整编辑器 URL；
- 不在重要科研 Figure 上进行第一次测试；
- 不向 Agent 提供密码、Cookie 或 AI Credits 信息。

## 3. 安装步骤

打开 Windows PowerShell：

```powershell
cd C:\bioagent
python --version
```

版本必须为 Python 3.11 或更高。创建虚拟环境：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

安装项目、开发工具和浏览器依赖：

```powershell
python -m pip install --upgrade pip
python -m pip install -e ".[dev,browser]"
python -m playwright install chromium
```

## 4. 验证安装

检查 CLI：

```powershell
python -m app.cli --help
```

执行不访问 BioRender 的 Dry Run：

```powershell
python -m app.cli demo
```

正常结果应包含：

```text
status: awaiting_confirmation
entities: 9
relations: 5
actions: 94
```

运行测试：

```powershell
python -m pytest
python -m ruff check app tests
```

浏览器测试只访问仓库内的兼容编辑器 Fixture，不会登录真实 BioRender。

## 5. 第一次连接 BioRender

打开持久浏览器 Profile：

```powershell
python -m app.cli browser-login
```

在可见浏览器中由用户手动登录。然后准备空白 Figure：

```powershell
$BlankFigureUrl = Read-Host "请输入可丢弃空白 Figure 的完整编辑器 URL"

python -m app.cli calibrate-ui `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

只测试普通素材搜索，不修改画布：

```powershell
python -m app.cli live-search-asset `
  --editor-url $BlankFigureUrl `
  --query "T cell" `
  --confirm-live
```

搜索并拖入一个素材：

```powershell
python -m app.cli phase0-search-drag `
  --editor-url $BlankFigureUrl `
  --query "T cell" `
  --confirm-live
```

## 6. 运行完整 Figure

内置 PD-1/PD-L1 Figure：

```powershell
python -m app.cli live-figure `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

明确流程 Prompt：

```powershell
python -m app.cli live-figure `
  --request "T cell → Tumor cell → Antibody" `
  --editor-url $BlankFigureUrl `
  --confirm-live
```

## 7. 运行文件位置

```text
C:\bioagent\runtime\agent.db
    SQLite 状态数据库

C:\bioagent\runtime\screenshots\
    Dry Run 截图

C:\bioagent\output\playwright\calibration\
    UI 校准 Profile 和截图

C:\bioagent\output\playwright\probes\
    单素材 Probe 证据

C:\bioagent\output\playwright\figures\<figure_id>\
    Live Figure 的逐动作证据
```

这些文件可能包含 Figure 内容或浏览器会话信息，不应提交到公开 GitHub 仓库。

## 8. 常见环境问题

### `python` 命令不存在

安装 Python 3.11+，并在安装时选择 `Add Python to PATH`。

### PowerShell 禁止激活虚拟环境

仅为当前窗口设置：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\.venv\Scripts\Activate.ps1
```

### 找不到 Playwright 或 Chromium

```powershell
python -m pip install -e ".[dev,browser]"
python -m playwright install chromium
```

### BioRender 登录失效

重新运行：

```powershell
python -m app.cli browser-login
```

账号和 MFA 仍必须由用户输入。

### 找不到搜索框、画布或工具

BioRender UI 可能发生变化。查看 Calibration 截图，不要改成盲目坐标点击。重新校准后仍
失败，应保留证据并停止。

### 出现 `blocked_by_policy`

说明检测到 AI、AI Credits、购买、订阅或升级上下文。不要绕过策略；关闭相关弹窗并返回
普通 Icons / Assets 搜索界面。

### 出现 `unknown` 或 `paused_reconciliation`

使用原 Figure ID 恢复：

```powershell
python -m app.cli resume-live-figure `
  --run-id "<figure_id>" `
  --confirm-live
```

恢复会先观察现场，不会盲目重复插入。

## 9. 环境验收清单

- [ ] Python 3.11+ 可用；
- [ ] 虚拟环境已激活；
- [ ] 项目依赖安装成功；
- [ ] Playwright Chromium 安装成功；
- [ ] `python -m app.cli demo` 成功；
- [ ] 用户能够手动登录 BioRender；
- [ ] 已准备可丢弃空白 Figure；
- [ ] L0 Calibration 成功；
- [ ] 未调用 BioRender AI；
- [ ] 未确认 AI Credits、购买或升级。

# BioRender GUI Agent Windows 安装指南

本指南面向 Windows 10/11、Windows PowerShell 5.1 或 PowerShell 7 用户。推荐使用
64 位 Python 3.12，也支持 Python 3.11。安装器不接受 Python 2、Microsoft Store
占位程序或未经本项目验证的 Python 3.13。

## 最简单安装方式

1. 将项目放在本地可写目录中；路径可以包含空格、中文或其他非 ASCII 字符。
2. 双击项目根目录的 `Install-BioAgent.cmd`。
3. 等待所有步骤显示 `[完成]`，记下日志路径。
4. 双击 `Start-BioAgent.cmd`。
5. 访问 `http://127.0.0.1:8000/ui`。

双击入口优先使用可正常运行的 PowerShell 7；未安装 PowerShell 7 时回退 Windows
PowerShell 5.1。`ExecutionPolicy Bypass` 只用于该次子进程，不修改系统执行策略。

安装依次完成 Windows 与 PowerShell 检查、Python 发现、`.venv` 创建、基础打包工具升级、
`.[browser]` 项目依赖安装、Playwright Chromium 安装、项目目录与 SQLite 初始化，以及
快速自检。默认不运行完整测试。

## PowerShell 安装方式

在项目根目录打开 PowerShell：

```powershell
.\scripts\install_windows.ps1
```

安装器按 `py -3.12`、`py -3.11`、`python`、`python3` 的顺序查找解释器。也可以明确指定：

```powershell
.\scripts\install_windows.ps1 -PythonPath "C:\Path To\Python312\python.exe"
```

未找到 Python 时，安装器会安全失败。只有用户显式添加 `-AllowWingetInstall`，并在交互提示
中确认后，才会用 `winget` 安装用户级 `Python.Python.3.12`。`-NonInteractive` 模式不会
自动确认系统软件安装。

## 启动和停止 Web UI

双击 `Start-BioAgent.cmd`，或运行：

```powershell
.\scripts\start_web_ui.ps1
```

脚本检查虚拟环境、核心导入和端口，然后执行：

```powershell
.\.venv\Scripts\python.exe -m app.cli web-ui --port 8000
```

服务只监听 `127.0.0.1`。按 `Ctrl+C` 停止。使用其他端口或禁止自动打开浏览器：

```powershell
.\scripts\start_web_ui.ps1 -Port 8010 -NoBrowser
```

端口已占用时脚本会在启动前停止并提示换端口，不会添加防火墙规则。

## 重复安装和修复

安装器是幂等的。重复运行会复用有效 `.venv`、由 pip 补齐依赖、保留 `.env`、SQLite、
截图、证据和旧日志，然后重新自检。它不会修改系统 PATH。

如果 `.venv` 缺少解释器、指向其他项目、损坏或使用不支持的 Python，安装器会停止，且不会
自行删除。确认 `.venv` 中没有需要保留的内容后运行：

```powershell
.\scripts\install_windows.ps1 -RecreateVenv
```

`-RecreateVenv` 只允许删除当前项目根目录内的 `.venv`，并在删除前显示绝对路径。

## 网络下载失败

pip 和 Chromium 下载步骤最多尝试 3 次，失败后返回非零退出码。pip 使用 120 秒连接超时，
Playwright 下载使用 120 秒连接超时。下载失败后可直接重新运行同一安装命令，无需删除
`.venv`。

需要显式镜像或代理时使用：

```powershell
.\scripts\install_windows.ps1 `
  -PipIndexUrl "https://pypi.tuna.tsinghua.edu.cn/simple" `
  -Proxy "http://proxy.example:8080"
```

安装器不会默认切换第三方镜像。代理值会在安装器日志中脱敏；仍应避免使用包含明文密码的
代理 URL。若 `pip` 已完成但 Chromium 下载失败，修复网络后重新运行即可。

暂时不下载浏览器可用 `-SkipBrowserInstall`，但此时不能运行需要 Chromium 的功能：

```powershell
.\scripts\install_windows.ps1 -SkipBrowserInstall
```

## 日志和故障定位

每次安装生成：

```text
output\install\install-YYYYMMDD-HHMMSS.log
```

日志包含 Windows、PowerShell、Python、虚拟环境、pip、步骤、命令和退出码，不应包含
BioRender Cookie、Token、密码、私密 Figure URL 或浏览器 Profile。失败时终端会显示失败
步骤、错误摘要、日志路径和建议修复方式。

常见问题：

- `未检测到受支持的 Python`：安装 Python 3.11/3.12，或使用 `-PythonPath`。
- `.venv 已损坏`：确认路径后使用 `-RecreateVenv`。
- `Chromium` 下载失败：检查代理、防火墙和磁盘空间，然后重复安装。
- 端口 `8000` 被占用：停止旧服务，或用 `-Port 8010` 启动。
- PowerShell 阻止直接运行：使用 CMD 双击入口，或仅对当前进程使用
  `powershell.exe -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1`。

## 开发者安装和测试

开发依赖以及完整测试仅在显式请求时安装/运行：

```powershell
.\scripts\install_windows.ps1 -Developer -RunTests
```

该模式安装 `.[browser,dev]`，随后运行 `pytest -q` 和 `ruff check .`。普通用户安装只执行
Python、pip、导入、CLI、SQLite 和本地 Chromium 快速自检。

## 安全卸载本地环境

先预览，不删除任何内容：

```powershell
.\scripts\uninstall_local_env.ps1
```

确认后只删除项目 `.venv`：

```powershell
.\scripts\uninstall_local_env.ps1 -ConfirmCleanup
```

`-RemoveRuntimeData` 额外选择 pytest 与测试浏览器临时目录；不会选择 SQLite、截图或运行
证据。浏览器登录 Profile 只有显式添加 `-RemoveBrowserProfile` 才会选择：

```powershell
.\scripts\uninstall_local_env.ps1 -ConfirmCleanup -RemoveBrowserProfile
```

卸载脚本不卸载系统 Python、PowerShell 或浏览器，不删除源码、Git 仓库、用户数据库、截图、
证据或安装日志。所有删除目标都必须通过项目根目录边界检查。

## BioRender 安全边界

环境安装不会要求或读取 BioRender 密码、Cookie、Token，不会自动登录，不会打开真实
Figure，不会执行 Live Run，也不会调用 BioRender AI Generate 或消耗 AI Credits。

安装成功只表示本地 Python、依赖、SQLite、CLI 和 Chromium 自检通过。首次连接 BioRender
仍须由用户在界面中人工启动登录、亲自输入账号/MFA，并使用可丢弃空白 Figure 分级验收。
本地安装成功不等于真实 BioRender 线上验收成功，也不等于 Windows 干净机验收完成。

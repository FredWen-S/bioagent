[CmdletBinding()]
param(
    [switch]$Developer,
    [switch]$RunTests,
    [switch]$RecreateVenv,
    [switch]$SkipBrowserInstall,
    [switch]$AllowWingetInstall,
    [string]$PythonPath,
    [string]$PipIndexUrl,
    [string]$Proxy,
    [switch]$NonInteractive
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$script:ProjectRoot = $null
$script:InstallerScriptRoot = $PSScriptRoot
$script:LogPath = $null
$script:FailureStep = "启动安装器"
$script:SensitiveValues = @()
if ($Proxy) { $script:SensitiveValues += $Proxy }
foreach ($proxyVariable in @("HTTP_PROXY", "HTTPS_PROXY")) {
    $existingProxy = [Environment]::GetEnvironmentVariable($proxyVariable)
    if ($existingProxy) { $script:SensitiveValues += $existingProxy }
}

function Resolve-ProjectRoot {
    return [System.IO.Path]::GetFullPath((Join-Path $script:InstallerScriptRoot ".."))
}

function Protect-LogText {
    param([AllowEmptyString()][string]$Text)

    $safeText = $Text
    foreach ($value in $script:SensitiveValues) {
        if ($value) { $safeText = $safeText.Replace($value, "<redacted>") }
    }
    return $safeText
}

function Write-InstallLog {
    param([AllowEmptyString()][string]$Message)

    if ($script:LogPath) {
        Add-Content -LiteralPath $script:LogPath -Value (Protect-LogText $Message) -Encoding UTF8
    }
}

function Write-Status {
    param(
        [Parameter(Mandatory = $true)][string]$Status,
        [Parameter(Mandatory = $true)][string]$Message,
        [ConsoleColor]$Color = [ConsoleColor]::Gray
    )

    $line = "[$Status] $Message"
    Write-Host $line -ForegroundColor $Color
    Write-InstallLog $line
}

function Format-CommandForLog {
    param([string]$FilePath, [string[]]$Arguments)

    $parts = @($FilePath) + @($Arguments | ForEach-Object {
        if ($_ -match '[\s"]') { '"' + $_.Replace('"', '\"') + '"' } else { $_ }
    })
    return Protect-LogText ($parts -join " ")
}

function Invoke-CheckedCommand {
    param(
        [Parameter(Mandatory = $true)][string]$Step,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [ValidateRange(1, 3)][int]$MaxAttempts = 1
    )

    $script:FailureStep = $Step
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        Write-Status "进行中" "$Step（尝试 $attempt/$MaxAttempts）" Cyan
        Write-InstallLog ("命令：" + (Format-CommandForLog $FilePath $Arguments))
        try {
            $oldErrorActionPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = "Continue"
                $commandOutput = & $FilePath @Arguments 2>&1
                $exitCode = $LASTEXITCODE
            } finally {
                $ErrorActionPreference = $oldErrorActionPreference
            }
            foreach ($line in @($commandOutput)) {
                $safeLine = Protect-LogText $line.ToString()
                Write-Host $safeLine
                Write-InstallLog $safeLine
            }
        } catch {
            $exitCode = 1
            Write-InstallLog ("命令异常：" + (Protect-LogText $_.Exception.Message))
        }

        Write-InstallLog "退出码：$exitCode"
        if ($exitCode -eq 0) {
            Write-Status "完成" $Step Green
            return
        }

        if ($attempt -lt $MaxAttempts) {
            $delay = [Math]::Pow(2, $attempt)
            Write-Status "警告" "$Step 失败，将在 $delay 秒后重试。" Yellow
            Start-Sleep -Seconds $delay
        }
    }
    throw "$Step 失败，命令退出码：$exitCode"
}

function Test-SupportedPythonVersion {
    param([int]$Major, [int]$Minor)
    return ($Major -eq 3 -and ($Minor -eq 11 -or $Minor -eq 12))
}

function Test-PythonCandidate {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$PrefixArguments = @()
    )

    try {
        $commandInfo = Get-Command $Command -ErrorAction Stop
        if ($commandInfo.Source -and $commandInfo.Source -match '\\WindowsApps\\') {
            return $null
        }
        $probe = 'import base64,sys;print(chr(124).join((str(sys.version_info[0]),str(sys.version_info[1]),sys.version.split()[0],base64.b64encode(sys.executable.encode()).decode())))'
        $result = & $Command @PrefixArguments -c $probe 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $result) { return $null }
        $payload = ([string]($result | Select-Object -Last 1)).Split('|')
        if ($payload.Count -ne 4) { return $null }
        $executable = [System.Text.Encoding]::UTF8.GetString(
            [System.Convert]::FromBase64String($payload[3])
        )
        if (-not (Test-SupportedPythonVersion -Major $payload[0] -Minor $payload[1])) {
            return [PSCustomObject]@{
                Supported = $false
                Version = $payload[2]
                Executable = $executable
            }
        }
        if ($executable -match '\\WindowsApps\\') { return $null }
        return [PSCustomObject]@{
            Supported = $true
            Version = $payload[2]
            Executable = [System.IO.Path]::GetFullPath($executable)
        }
    } catch {
        return $null
    }
}

function Find-SupportedPython {
    param([string]$ExplicitPath)

    if ($ExplicitPath) {
        if (-not (Test-Path -LiteralPath $ExplicitPath -PathType Leaf)) {
            throw "-PythonPath 指定的文件不存在：$ExplicitPath"
        }
        $resolved = (Resolve-Path -LiteralPath $ExplicitPath).ProviderPath
        $candidate = Test-PythonCandidate -Command $resolved
        if ($null -eq $candidate) {
            throw "-PythonPath 指定的解释器无法运行或是 Microsoft Store 占位程序：$resolved"
        }
        if (-not $candidate.Supported) {
            throw "不支持 Python $($candidate.Version)。请选择 Python 3.11 或 3.12。"
        }
        return $candidate
    }

    $unsupported = @()
    $candidates = @(
        @{ Command = "py"; Arguments = @("-3.12") },
        @{ Command = "py"; Arguments = @("-3.11") },
        @{ Command = "python"; Arguments = @() },
        @{ Command = "python3"; Arguments = @() }
    )
    foreach ($item in $candidates) {
        $candidate = Test-PythonCandidate -Command $item.Command -PrefixArguments $item.Arguments
        if ($null -eq $candidate) { continue }
        if ($candidate.Supported) { return $candidate }
        $unsupported += "$($candidate.Version) at $($candidate.Executable)"
    }
    if ($unsupported.Count -gt 0) {
        Write-Status "警告" ("发现不受支持的 Python：" + ($unsupported -join "; ")) Yellow
    }
    return $null
}

function Install-PythonWithWinget {
    if ($NonInteractive) {
        throw "非交互模式下不能确认系统软件安装。请先安装 Python 3.11/3.12，或移除 -NonInteractive。"
    }
    if ($null -eq (Get-Command winget -ErrorAction SilentlyContinue)) {
        throw "未找到 winget。请从 python.org 安装 Python 3.11 或 3.12。"
    }

    $packageId = "Python.Python.3.12"
    Write-Status "警告" "准备使用 winget 安装用户级软件包：$packageId" Yellow
    $answer = Read-Host "是否继续？输入 Y 确认"
    if ($answer -notmatch '^[Yy]$') { throw "用户取消了 Python 安装。" }
    Invoke-CheckedCommand -Step "使用 winget 安装 Python 3.12" -FilePath "winget" -Arguments @(
        "install", "--id", $packageId, "--exact", "--scope", "user",
        "--accept-source-agreements", "--accept-package-agreements"
    )
}

function Assert-ProjectChildPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $root = [System.IO.Path]::GetFullPath($script:ProjectRoot).TrimEnd('\', '/')
    $target = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $prefix = $root + [System.IO.Path]::DirectorySeparatorChar
    if (-not $target.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝操作项目根目录之外的路径：$target"
    }
    return $target
}

function Test-VenvPython {
    param([Parameter(Mandatory = $true)][string]$VenvPython)

    if (-not (Test-Path -LiteralPath $VenvPython -PathType Leaf)) { return $false }
    try {
        $expected = [System.IO.Path]::GetFullPath((Join-Path $script:ProjectRoot ".venv"))
        $probe = 'import os,sys;print(chr(124).join((str(sys.version_info[0]),str(sys.version_info[1]),str(int(os.path.normcase(os.path.abspath(sys.prefix))==os.path.normcase(os.path.abspath(sys.argv[1])))),str(int(sys.prefix!=sys.base_prefix)))))'
        $result = & $VenvPython -c $probe $expected 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $result) { return $false }
        $payload = ([string]($result | Select-Object -Last 1)).Split('|')
        if ($payload.Count -ne 4) { return $false }
        if (-not (Test-SupportedPythonVersion $payload[0] $payload[1])) { return $false }
        return ($payload[2] -eq "1" -and $payload[3] -eq "1")
    } catch {
        return $false
    }
}

function Get-PipNetworkArguments {
    $arguments = @("--timeout", "120", "--retries", "2")
    if ($PipIndexUrl) { $arguments += @("--index-url", $PipIndexUrl) }
    if ($Proxy) { $arguments += @("--proxy", $Proxy) }
    return $arguments
}

function Initialize-EnvironmentFile {
    $examplePath = Join-Path $script:ProjectRoot ".env.example"
    $environmentPath = Join-Path $script:ProjectRoot ".env"
    if ((Test-Path -LiteralPath $examplePath -PathType Leaf) -and
        -not (Test-Path -LiteralPath $environmentPath)) {
        Copy-Item -LiteralPath $examplePath -Destination $environmentPath
        Write-Status "完成" "已从 .env.example 创建 .env（未写入任何凭据）。" Green
    } elseif (Test-Path -LiteralPath $environmentPath) {
        Write-Status "跳过" "已存在 .env，未覆盖用户配置。" DarkGray
    } else {
        Write-Status "跳过" "项目不需要 .env。" DarkGray
    }
}

function Invoke-Installer {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
        throw "此安装脚本仅支持 Windows 10/11。"
    }
    if ($PSVersionTable.PSVersion -lt [Version]"5.1") {
        throw "PowerShell 版本过低。需要 Windows PowerShell 5.1 或 PowerShell 7。"
    }

    $script:ProjectRoot = Resolve-ProjectRoot
    Set-Location $script:ProjectRoot
    $logDirectory = Join-Path $script:ProjectRoot "output\install"
    New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null
    $script:LogPath = Join-Path $logDirectory ("install-{0}.log" -f (Get-Date -Format "yyyyMMdd-HHmmss"))
    New-Item -ItemType File -Path $script:LogPath -Force | Out-Null

    Write-Status "检查" "项目根目录：$script:ProjectRoot" Cyan
    Write-InstallLog "Windows：$([Environment]::OSVersion.VersionString)"
    Write-InstallLog "PowerShell：$($PSVersionTable.PSVersion)"
    $principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    $isAdmin = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    Write-InstallLog "管理员权限：$isAdmin（安装不要求管理员权限）"

    if (-not (Test-Path -LiteralPath (Join-Path $script:ProjectRoot "pyproject.toml"))) {
        throw "项目根目录缺少 pyproject.toml，无法确认依赖管理方式。"
    }

    $script:FailureStep = "检查 Python"
    Write-Status "检查" "正在查找 Python 3.12 或 3.11..." Cyan
    $python = Find-SupportedPython -ExplicitPath $PythonPath
    if ($null -eq $python -and $AllowWingetInstall) {
        Install-PythonWithWinget
        $python = Find-SupportedPython
    }
    if ($null -eq $python) {
        throw "未检测到受支持的 Python。请安装 Python 3.11 或 3.12，并勾选 Add Python to PATH。也可显式传入 -PythonPath 或 -AllowWingetInstall。"
    }
    Write-Status "完成" "Python $($python.Version)：$($python.Executable)" Green
    Write-InstallLog "Python 路径：$($python.Executable)"

    $script:FailureStep = "检查虚拟环境"
    $venvPath = Join-Path $script:ProjectRoot ".venv"
    $venvPython = Join-Path $venvPath "Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPath) {
        if (Test-VenvPython -VenvPython $venvPython) {
            Write-Status "跳过" "现有 .venv 有效，将复用并补齐依赖。" DarkGray
        } elseif (-not $RecreateVenv) {
            throw "现有 .venv 已损坏、不属于当前项目或 Python 版本不受支持。请检查 $venvPath；确认可删除后使用 -RecreateVenv。"
        } else {
            $safeVenvPath = Assert-ProjectChildPath $venvPath
            Write-Status "警告" "将按 -RecreateVenv 删除：$safeVenvPath" Yellow
            Remove-Item -LiteralPath $safeVenvPath -Recurse -Force
        }
    }
    if (-not (Test-Path -LiteralPath $venvPath)) {
        Invoke-CheckedCommand -Step "创建项目虚拟环境" -FilePath $python.Executable -Arguments @("-m", "venv", $venvPath)
    }
    if (-not (Test-VenvPython -VenvPython $venvPython)) {
        throw "虚拟环境创建后验证失败：$venvPython"
    }
    Write-InstallLog "虚拟环境：$venvPath"

    $pipNetwork = Get-PipNetworkArguments
    Invoke-CheckedCommand -Step "升级 pip、setuptools 和 wheel" -FilePath $venvPython -Arguments (
        @("-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel") + $pipNetwork
    ) -MaxAttempts 3

    $installTarget = ".[browser]"
    if ($Developer -or $RunTests) { $installTarget = ".[browser,dev]" }
    if ($RunTests -and -not $Developer) {
        Write-Status "警告" "-RunTests 会同时安装 dev 依赖。" Yellow
    }
    Invoke-CheckedCommand -Step "安装 BioRender GUI Agent 项目依赖" -FilePath $venvPython -Arguments (
        @("-m", "pip", "install", "-e", $installTarget) + $pipNetwork
    ) -MaxAttempts 3

    if ($SkipBrowserInstall) {
        Write-Status "跳过" "已按 -SkipBrowserInstall 跳过 Chromium 下载。" DarkGray
    } else {
        $oldTimeout = $env:PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT
        $oldHttpsProxy = $env:HTTPS_PROXY
        $env:PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT = "120000"
        if ($Proxy) { $env:HTTPS_PROXY = $Proxy }
        try {
            Invoke-CheckedCommand -Step "安装 Playwright Chromium" -FilePath $venvPython -Arguments @(
                "-m", "playwright", "install", "chromium"
            ) -MaxAttempts 3
        } finally {
            $env:PLAYWRIGHT_DOWNLOAD_CONNECTION_TIMEOUT = $oldTimeout
            $env:HTTPS_PROXY = $oldHttpsProxy
        }
    }

    $script:FailureStep = "初始化配置"
    Initialize-EnvironmentFile
    Invoke-CheckedCommand -Step "初始化项目目录和 SQLite" -FilePath $venvPython -Arguments @(
        "-c",
        "from app.config import settings; settings.ensure_directories(); from app.storage.database import FigureDatabase; print(FigureDatabase().path)"
    )

    Invoke-CheckedCommand -Step "检查 Python 版本" -FilePath $venvPython -Arguments @("--version")
    Invoke-CheckedCommand -Step "检查 Python 包依赖" -FilePath $venvPython -Arguments @("-m", "pip", "check")
    Invoke-CheckedCommand -Step "检查 FastAPI 和 Playwright 导入" -FilePath $venvPython -Arguments @(
        "-c", "import fastapi, playwright; print('Core imports: OK')"
    )
    Invoke-CheckedCommand -Step "检查 BioRender GUI Agent CLI" -FilePath $venvPython -Arguments @("-m", "app.cli", "--help")

    if (-not $SkipBrowserInstall) {
        $browserCheck = "from playwright.sync_api import sync_playwright; p=sync_playwright().start(); b=p.chromium.launch(headless=True); page=b.new_page(); page.set_content('<html><body>ok</body></html>'); assert page.text_content('body')=='ok'; b.close(); p.stop(); print('Chromium: OK')"
        Invoke-CheckedCommand -Step "验证本地 Chromium 可启动" -FilePath $venvPython -Arguments @("-c", $browserCheck)
    }

    if ($RunTests) {
        Invoke-CheckedCommand -Step "运行 pytest" -FilePath $venvPython -Arguments @("-m", "pytest", "-q")
        Invoke-CheckedCommand -Step "运行 Ruff" -FilePath $venvPython -Arguments @("-m", "ruff", "check", ".")
    }

    $pipVersion = & $venvPython -m pip --version 2>&1
    Write-InstallLog ("pip：" + (($pipVersion | ForEach-Object { $_.ToString() }) -join " "))
    Write-Status "完成" "Windows 本地运行环境已准备完成。" Green
    Write-Host "启动方式：双击 Start-BioAgent.cmd"
    Write-Host "访问地址：http://127.0.0.1:8000/ui"
    Write-Host "安装日志：$script:LogPath"
    Write-Host "BioRender 登录必须由用户在界面中人工完成。"
}

if ($env:BIOAGENT_INSTALLER_IMPORT_ONLY -ne "1") {
    try {
        Invoke-Installer
        exit 0
    } catch {
        $message = Protect-LogText $_.Exception.Message
        Write-Status "失败" "失败步骤：$script:FailureStep" Red
        Write-Status "失败" "错误摘要：$message" Red
        if ($script:LogPath) { Write-Host "日志路径：$script:LogPath" }
        if ($script:FailureStep -match "Chromium|Playwright") {
            Write-Host "建议：检查网络后重新运行同一脚本；无需删除 .venv。"
        } elseif ($message -match "Python|.venv") {
            Write-Host "建议：安装 Python 3.11/3.12；损坏的虚拟环境可在确认后使用 -RecreateVenv。"
        } else {
            Write-Host "建议：查看日志中的首个失败命令，修复后重新运行同一脚本。"
        }
        exit 1
    }
}

[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Port = 8000,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$script:StartScriptRoot = $PSScriptRoot

function Get-ProjectRoot {
    return [System.IO.Path]::GetFullPath((Join-Path $script:StartScriptRoot ".."))
}

function Test-LocalPortAvailable {
    param([Parameter(Mandatory = $true)][int]$PortNumber)

    $listener = New-Object System.Net.Sockets.TcpListener(
        [System.Net.IPAddress]::Loopback,
        $PortNumber
    )
    try {
        $listener.Start()
        return $true
    } catch [System.Net.Sockets.SocketException] {
        return $false
    } finally {
        try { $listener.Stop() } catch { }
    }
}

function Invoke-StartWebUi {
    $projectRoot = Get-ProjectRoot
    $venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
    $url = "http://127.0.0.1:$Port/ui"

    if (-not (Test-Path -LiteralPath $venvPython -PathType Leaf)) {
        throw "未找到项目虚拟环境。请先双击 Install-BioAgent.cmd，或运行 scripts\install_windows.ps1。"
    }

    Write-Host "[检查] 正在验证本地运行环境..."
    $oldErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $checkOutput = & $venvPython -c "import fastapi, playwright; import app.cli" 2>&1
        $checkExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $oldErrorActionPreference
    }
    if ($checkExitCode -ne 0) {
        $detail = ($checkOutput | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
        throw "环境自检失败。请重新运行安装脚本。$([Environment]::NewLine)$detail"
    }

    if (-not (Test-LocalPortAvailable -PortNumber $Port)) {
        throw "端口 $Port 已被占用。请关闭占用程序，或运行 scripts\start_web_ui.ps1 -Port <其他端口>。"
    }

    Write-Host "[完成] 环境自检通过。"
    Write-Host "[进行中] 正在启动 BioRender GUI Agent：$url"
    Write-Host "按 Ctrl+C 停止服务。"

    if (-not $NoBrowser) {
        try {
            Start-Process $url
        } catch {
            Write-Warning "无法自动打开默认浏览器，请手动访问 $url"
        }
    }

    Push-Location $projectRoot
    try {
        & $venvPython -m app.cli web-ui --port $Port
        if ($LASTEXITCODE -ne 0) {
            throw "Web UI 进程退出，退出码：$LASTEXITCODE"
        }
    } finally {
        Pop-Location
    }
}

if ($env:BIOAGENT_START_IMPORT_ONLY -ne "1") {
    try {
        Invoke-StartWebUi
    } catch {
        Write-Host "[失败] $($_.Exception.Message)" -ForegroundColor Red
        exit 1
    }
}

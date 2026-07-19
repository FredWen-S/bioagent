$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (Test-Path -LiteralPath $venvPython) {
    $pythonCommand = $venvPython
} else {
    $pythonInfo = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonInfo) {
        Write-Error "未找到 Python。请先按照 docs/Environment_Setup.md 配置项目环境。"
    }
    $pythonCommand = $pythonInfo.Source
}

Write-Host "BioRender GUI Agent Web UI:"
Write-Host "http://127.0.0.1:8000/ui"
Write-Host "按 Ctrl+C 停止服务。"

Push-Location $projectRoot
try {
    & $pythonCommand -m app.cli web-ui
} finally {
    Pop-Location
}

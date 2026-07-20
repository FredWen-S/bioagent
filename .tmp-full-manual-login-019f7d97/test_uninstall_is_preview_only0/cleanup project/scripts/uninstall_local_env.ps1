[CmdletBinding()]
param(
    [switch]$ConfirmCleanup,
    [switch]$RemoveRuntimeData,
    [switch]$RemoveBrowserProfile
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "..")).TrimEnd('\', '/')

function Assert-ProjectChildPath {
    param([Parameter(Mandatory = $true)][string]$Path)

    $target = [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
    $prefix = $projectRoot + [System.IO.Path]::DirectorySeparatorChar
    if (-not $target.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "拒绝删除项目根目录之外的路径：$target"
    }
    return $target
}

$targets = New-Object System.Collections.Generic.List[string]
$targets.Add((Join-Path $projectRoot ".venv"))

if ($RemoveRuntimeData) {
    $targets.Add((Join-Path $projectRoot "runtime\pytest-temp"))
    $runtimeRoot = Join-Path $projectRoot "runtime"
    if (Test-Path -LiteralPath $runtimeRoot) {
        Get-ChildItem -LiteralPath $runtimeRoot -Directory -Filter "browser-*" | ForEach-Object {
            $targets.Add($_.FullName)
        }
    }
}
if ($RemoveBrowserProfile) {
    $targets.Add((Join-Path $projectRoot "runtime\sessions\biorender-profile"))
}

Write-Host "[检查] 项目根目录：$projectRoot"
Write-Host "[检查] 以下是本次选中的清理目标："
foreach ($target in $targets) {
    $safeTarget = Assert-ProjectChildPath $target
    $state = if (Test-Path -LiteralPath $safeTarget) { "存在" } else { "不存在" }
    Write-Host "  - $safeTarget（$state）"
}
Write-Host "[跳过] 不会删除源码、Git、SQLite 数据库、截图、运行证据或安装日志。"
if (-not $RemoveBrowserProfile) {
    Write-Host "[跳过] 浏览器登录 Profile 未被选择。"
}

if (-not $ConfirmCleanup) {
    Write-Host "[预览] 未执行删除。确认后请重新运行并添加 -ConfirmCleanup。"
    exit 0
}

foreach ($target in $targets) {
    $safeTarget = Assert-ProjectChildPath $target
    if (Test-Path -LiteralPath $safeTarget) {
        $item = Get-Item -LiteralPath $safeTarget -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "拒绝递归删除重解析点：$safeTarget"
        }
        Write-Host "[进行中] 删除：$safeTarget"
        Remove-Item -LiteralPath $safeTarget -Recurse -Force
        Write-Host "[完成] 已删除：$safeTarget"
    }
}

Write-Host "[完成] 项目本地环境清理完成。"

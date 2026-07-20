[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Mode,
    [Parameter(Mandatory = $true)][string]$InstallerPath,
    [string]$Value,
    [string]$ProjectRootOverride,
    [string]$StartScriptPath
)

$ErrorActionPreference = "Stop"
$env:BIOAGENT_INSTALLER_IMPORT_ONLY = "1"
. $InstallerPath

switch ($Mode) {
    "root" {
        Write-Output (Resolve-ProjectRoot)
    }
    "versions" {
        [PSCustomObject]@{
            python311 = Test-SupportedPythonVersion 3 11
            python312 = Test-SupportedPythonVersion 3 12
            python313 = Test-SupportedPythonVersion 3 13
            python2 = Test-SupportedPythonVersion 2 7
        } | ConvertTo-Json -Compress
    }
    "python-probe" {
        $candidate = Test-PythonCandidate -Command $Value
        if ($null -eq $candidate) {
            Write-Output "null"
        } else {
            $candidate | ConvertTo-Json -Compress
        }
    }
    "path-guard" {
        $script:ProjectRoot = $ProjectRootOverride
        try {
            Assert-ProjectChildPath $Value | Out-Null
            Write-Output "allowed"
        } catch {
            Write-Output "blocked"
        }
    }
    "venv-probe" {
        $script:ProjectRoot = $ProjectRootOverride
        Write-Output (Test-VenvPython -VenvPython $Value)
    }
    "start-port" {
        $env:BIOAGENT_START_IMPORT_ONLY = "1"
        . $StartScriptPath
        Write-Output (Test-LocalPortAvailable -PortNumber ([int]$Value))
    }
    "command-failure" {
        try {
            Invoke-CheckedCommand -Step "intentional failure" -FilePath "cmd.exe" -Arguments @(
                "/c", "exit", "7"
            )
            Write-Output "unexpected-success"
        } catch {
            Write-Output "failed-as-expected"
        }
    }
    "env-file" {
        $script:ProjectRoot = $ProjectRootOverride
        Initialize-EnvironmentFile
        Get-Content -Raw -LiteralPath (Join-Path $script:ProjectRoot ".env")
    }
    default {
        throw "Unknown harness mode: $Mode"
    }
}

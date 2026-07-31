$ErrorActionPreference = "Stop"

$ProjectRoot = $PSScriptRoot
$DefaultInstallDirectory = Join-Path `
    ([Environment]::GetFolderPath("LocalApplicationData")) `
    "IELTS Codex\bin"
$InstallDirectory = if ($env:IELTS_CODEX_INSTALL_DIR) {
    [System.IO.Path]::GetFullPath($env:IELTS_CODEX_INSTALL_DIR)
} else {
    $DefaultInstallDirectory
}
$CommandShim = Join-Path $InstallDirectory "ielts.cmd"
$PowerShellShim = Join-Path $InstallDirectory "ielts.ps1"
$CommandMarker = "IELTS Codex source launcher"

function Write-Info {
    param([string]$Message)

    Write-Host "IELTS Codex: $Message"
}

function Assert-SafeTarget {
    param([string]$Path)

    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $content = Get-Content -LiteralPath $Path -Raw -ErrorAction SilentlyContinue
    if ($null -eq $content -or -not $content.Contains($CommandMarker)) {
        throw "Refusing to replace an unrelated command at $Path."
    }
}

function Add-InstallDirectoryToUserPath {
    $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $entries = @(
        ([string]$userPath -split ";") |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    $normalizedInstall = $InstallDirectory.TrimEnd("\")
    foreach ($entry in $entries) {
        if ([string]::Equals(
            $entry.Trim().TrimEnd("\"),
            $normalizedInstall,
            [StringComparison]::OrdinalIgnoreCase
        )) {
            return $false
        }
    }
    $newPath = if ([string]::IsNullOrWhiteSpace($userPath)) {
        $InstallDirectory
    } else {
        "$($userPath.TrimEnd(';'));$InstallDirectory"
    }
    [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
    return $true
}

try {
    Assert-SafeTarget $CommandShim
    Assert-SafeTarget $PowerShellShim

    Write-Info "Checking the Python 3.10+ launcher before installing the command..."
    & (Join-Path $ProjectRoot "run.bat") --version
    if ($LASTEXITCODE -ne 0) {
        throw "The Windows launcher check failed with exit code $LASTEXITCODE."
    }

    New-Item -ItemType Directory -Force -Path $InstallDirectory | Out-Null
    $escapedLauncher = (Join-Path $ProjectRoot "run.ps1").Replace("'", "''")
    $powerShellContent = @"
# IELTS Codex source launcher
`$launcher = '$escapedLauncher'
if (-not (Test-Path -LiteralPath `$launcher -PathType Leaf)) {
    [Console]::Error.WriteLine(
        "IELTS Codex: source checkout not found at {0}. Re-run install.bat.",
        `$launcher
    )
    exit 1
}
& `$launcher @args
exit `$LASTEXITCODE
"@
    $commandContent = @"
@echo off
rem IELTS Codex source launcher
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0ielts.ps1" %*
exit /b %ERRORLEVEL%
"@
    $utf8Bom = New-Object System.Text.UTF8Encoding($true)
    [IO.File]::WriteAllText($PowerShellShim, $powerShellContent, $utf8Bom)
    [IO.File]::WriteAllText(
        $CommandShim,
        $commandContent,
        [Text.Encoding]::ASCII
    )
    Write-Info "Installed the ielts command at $CommandShim."

    if ($env:IELTS_CODEX_INSTALL_DIR) {
        Write-Info "Custom install directory used; add $InstallDirectory to PATH if needed."
        exit 0
    }
    if (Add-InstallDirectoryToUserPath) {
        Write-Info "Added $InstallDirectory to your user PATH."
        Write-Info "Open a new terminal, then run 'ielts'."
    } else {
        Write-Info "Run 'ielts' to open the interface."
    }
} catch {
    [Console]::Error.WriteLine("IELTS Codex: {0}", $_.Exception.Message)
    exit 1
}

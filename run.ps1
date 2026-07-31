$ErrorActionPreference = "Stop"
$AppArguments = @($args)

$MinimumPython = "3.10"
$ManagedPython = "3.12"
$UvInstallVersion = "0.11.32"
$ProjectRoot = $PSScriptRoot
$BootstrapRoot = if ($env:IELTS_CODEX_BOOTSTRAP_DIR) {
    [System.IO.Path]::GetFullPath($env:IELTS_CODEX_BOOTSTRAP_DIR)
} else {
    Join-Path $ProjectRoot ".ielts-bootstrap"
}

function Write-Info {
    param([string]$Message)

    Write-Host "IELTS Codex: $Message"
}

function Test-PythonCandidate {
    param(
        [string]$Executable,
        [string[]]$PrefixArguments = @()
    )

    try {
        & $Executable @PrefixArguments -c `
            "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" `
            *> $null
        if ($LASTEXITCODE -eq 0) {
            return [PSCustomObject]@{
                Executable = $Executable
                PrefixArguments = @($PrefixArguments)
            }
        }
    } catch {
        return $null
    }
    return $null
}

function Find-ManagedPython {
    $pythonRoot = Join-Path $BootstrapRoot "python"
    if (-not (Test-Path -LiteralPath $pythonRoot -PathType Container)) {
        return $null
    }

    $localUv = Join-Path $BootstrapRoot "bin\uv.exe"
    if (Test-Path -LiteralPath $localUv -PathType Leaf) {
        $oldCache = $env:UV_CACHE_DIR
        $oldInstallDir = $env:UV_PYTHON_INSTALL_DIR
        $oldPreference = $env:UV_PYTHON_PREFERENCE
        try {
            $env:UV_CACHE_DIR = Join-Path $BootstrapRoot "cache"
            $env:UV_PYTHON_INSTALL_DIR = $pythonRoot
            $env:UV_PYTHON_PREFERENCE = "only-managed"
            $paths = @(& $localUv python find $ManagedPython 2>$null)
            if ($LASTEXITCODE -eq 0 -and $paths.Count -gt 0) {
                $candidate = Test-PythonCandidate `
                    -Executable ([string]$paths[-1]).Trim()
                if ($null -ne $candidate) {
                    return $candidate
                }
            }
        } catch {
            # Fall through to the directory scan for an interrupted bootstrap.
        } finally {
            $env:UV_CACHE_DIR = $oldCache
            $env:UV_PYTHON_INSTALL_DIR = $oldInstallDir
            $env:UV_PYTHON_PREFERENCE = $oldPreference
        }
    }

    $relativeExecutables = @(
        "python.exe",
        "bin\python.exe",
        "bin\python3.12.exe"
    )
    $installations = @(
        Get-ChildItem -LiteralPath $pythonRoot -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending
    )
    foreach ($installation in $installations) {
        foreach ($relativePath in $relativeExecutables) {
            $executable = Join-Path $installation.FullName $relativePath
            if (Test-Path -LiteralPath $executable -PathType Leaf) {
                $candidate = Test-PythonCandidate -Executable $executable
                if ($null -ne $candidate) {
                    return $candidate
                }
            }
        }
    }
    return $null
}

function Find-Python {
    $candidates = @(
        [PSCustomObject]@{ Executable = "python"; PrefixArguments = @() },
        [PSCustomObject]@{ Executable = "python3"; PrefixArguments = @() },
        [PSCustomObject]@{ Executable = "py"; PrefixArguments = @("-3.14") },
        [PSCustomObject]@{ Executable = "py"; PrefixArguments = @("-3.13") },
        [PSCustomObject]@{ Executable = "py"; PrefixArguments = @("-3.12") },
        [PSCustomObject]@{ Executable = "py"; PrefixArguments = @("-3.11") },
        [PSCustomObject]@{ Executable = "py"; PrefixArguments = @("-3.10") }
    )
    foreach ($candidate in $candidates) {
        $found = Test-PythonCandidate `
            -Executable $candidate.Executable `
            -PrefixArguments $candidate.PrefixArguments
        if ($null -ne $found) {
            return $found
        }
    }
    return (Find-ManagedPython)
}

function Confirm-ManagedPython {
    $prompt = "IELTS Codex: No Python $MinimumPython+ interpreter was found. " +
        "Use Astral uv (downloading pinned uv if needed) to install project-local " +
        "Python $ManagedPython into $BootstrapRoot? This will not change the " +
        "system Python or PATH. [y/N]"
    try {
        $answer = Read-Host $prompt
    } catch {
        return $false
    }
    return $answer -match "^(?i:y|yes)$"
}

function Install-LocalUv {
    $uvDirectory = Join-Path $BootstrapRoot "bin"
    $uvExecutable = Join-Path $uvDirectory "uv.exe"
    $installerUrl = "https://astral.sh/uv/$UvInstallVersion/install.ps1"
    $installerFile = Join-Path `
        ([System.IO.Path]::GetTempPath()) `
        ("ielts-codex-uv-{0}.ps1" -f ([Guid]::NewGuid()).ToString("N"))

    New-Item -ItemType Directory -Force -Path $uvDirectory | Out-Null
    Write-Info "Downloading pinned uv $UvInstallVersion..."
    try {
        [Net.ServicePointManager]::SecurityProtocol = `
            [Net.ServicePointManager]::SecurityProtocol -bor `
            [Net.SecurityProtocolType]::Tls12
        $oldProgress = $ProgressPreference
        $ProgressPreference = "SilentlyContinue"
        try {
            Invoke-WebRequest `
                -UseBasicParsing `
                -Uri $installerUrl `
                -OutFile $installerFile
        } finally {
            $ProgressPreference = $oldProgress
        }

        $oldUnmanagedInstall = $env:UV_UNMANAGED_INSTALL
        $oldNoModifyPath = $env:UV_NO_MODIFY_PATH
        try {
            $env:UV_UNMANAGED_INSTALL = $uvDirectory
            $env:UV_NO_MODIFY_PATH = "1"
            Write-Info "Installing uv $UvInstallVersion inside the project bootstrap directory..."
            $childPowerShell = Join-Path $PSHOME "powershell.exe"
            & $childPowerShell `
                -NoLogo `
                -NoProfile `
                -NonInteractive `
                -ExecutionPolicy Bypass `
                -File $installerFile | Out-Host
            $installerExitCode = $LASTEXITCODE
        } finally {
            $env:UV_UNMANAGED_INSTALL = $oldUnmanagedInstall
            $env:UV_NO_MODIFY_PATH = $oldNoModifyPath
        }
        if ($installerExitCode -ne 0) {
            throw "The pinned uv installer exited with code $installerExitCode."
        }
    } catch {
        throw "Could not install pinned uv from $installerUrl. $($_.Exception.Message)"
    } finally {
        Remove-Item -LiteralPath $installerFile -Force -ErrorAction SilentlyContinue
    }

    if (-not (Test-Path -LiteralPath $uvExecutable -PathType Leaf)) {
        throw "uv installation completed, but uv.exe was not found."
    }
    return $uvExecutable
}

function Resolve-Uv {
    $uvCommand = Get-Command "uv" -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($null -ne $uvCommand) {
        Write-Info "Using existing uv executable at $($uvCommand.Source)."
        return $uvCommand.Source
    }

    $localUv = Join-Path $BootstrapRoot "bin\uv.exe"
    if (Test-Path -LiteralPath $localUv -PathType Leaf) {
        return $localUv
    }
    return (Install-LocalUv)
}

function Install-ManagedPython {
    $uvExecutable = Resolve-Uv
    $pythonDirectory = Join-Path $BootstrapRoot "python"
    $cacheDirectory = Join-Path $BootstrapRoot "cache"
    New-Item -ItemType Directory -Force -Path $pythonDirectory | Out-Null
    New-Item -ItemType Directory -Force -Path $cacheDirectory | Out-Null

    $oldCache = $env:UV_CACHE_DIR
    $oldInstallDir = $env:UV_PYTHON_INSTALL_DIR
    $oldInstallBin = $env:UV_PYTHON_INSTALL_BIN
    $oldPreference = $env:UV_PYTHON_PREFERENCE
    try {
        $env:UV_CACHE_DIR = $cacheDirectory
        $env:UV_PYTHON_INSTALL_DIR = $pythonDirectory
        $env:UV_PYTHON_INSTALL_BIN = "false"
        Write-Info "Installing project-local Python $ManagedPython with uv..."
        & $uvExecutable python install $ManagedPython | Out-Host
        $installExitCode = $LASTEXITCODE
        if ($installExitCode -ne 0) {
            throw "uv could not install Python $ManagedPython."
        }

        $env:UV_PYTHON_PREFERENCE = "only-managed"
        $paths = @(& $uvExecutable python find $ManagedPython)
        $findExitCode = $LASTEXITCODE
        if ($findExitCode -ne 0 -or $paths.Count -eq 0) {
            throw "uv installed Python, but could not locate it."
        }
        $managedPath = ([string]$paths[-1]).Trim()
    } finally {
        $env:UV_CACHE_DIR = $oldCache
        $env:UV_PYTHON_INSTALL_DIR = $oldInstallDir
        $env:UV_PYTHON_INSTALL_BIN = $oldInstallBin
        $env:UV_PYTHON_PREFERENCE = $oldPreference
    }

    $candidate = Test-PythonCandidate -Executable $managedPath
    if ($null -eq $candidate) {
        throw "uv finished, but no Python $MinimumPython+ interpreter was found."
    }
    return $candidate
}

function Invoke-IeltsCodex {
    if ($env:IELTS_CODEX_PYTHON) {
        $python = Test-PythonCandidate -Executable $env:IELTS_CODEX_PYTHON
        if ($null -eq $python) {
            throw "IELTS_CODEX_PYTHON must name a Python $MinimumPython+ interpreter."
        }
    } else {
        $python = Find-Python
    }

    if ($null -eq $python) {
        if ($env:IELTS_CODEX_NO_AUTO_INSTALL -eq "1") {
            throw "Python $MinimumPython+ is required. Set IELTS_CODEX_PYTHON or install it first."
        }
        if (-not (Confirm-ManagedPython)) {
            throw "Python $MinimumPython+ is required. Rerun run.bat and approve the uv-managed project-local Python."
        }
        $python = Install-ManagedPython
    }

    $prefixArguments = @($python.PrefixArguments)
    $versionOutput = @(
        & $python.Executable @prefixArguments -c `
            "import sys; print(sys.version.split()[0])"
    )
    if ($LASTEXITCODE -ne 0 -or $versionOutput.Count -eq 0) {
        throw "The selected Python interpreter could not start."
    }
    Write-Info "Using $(([string]$versionOutput[-1]).Trim())."

    $entryPoint = Join-Path $ProjectRoot "ielts.py"
    & $python.Executable @prefixArguments $entryPoint @AppArguments
    exit $LASTEXITCODE
}

try {
    Invoke-IeltsCodex
} catch {
    [Console]::Error.WriteLine("IELTS Codex: {0}", $_.Exception.Message)
    exit 1
}

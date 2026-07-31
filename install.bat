@echo off
setlocal EnableExtensions DisableDelayedExpansion

set "IELTS_CODEX_POWERSHELL=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%IELTS_CODEX_POWERSHELL%" set "IELTS_CODEX_POWERSHELL=powershell.exe"

"%IELTS_CODEX_POWERSHELL%" -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
set "IELTS_CODEX_EXIT_CODE=%ERRORLEVEL%"
endlocal & exit /b %IELTS_CODEX_EXIT_CODE%

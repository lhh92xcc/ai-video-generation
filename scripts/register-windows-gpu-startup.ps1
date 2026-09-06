[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$TaskName = "AI Video Generation - Windows GPU Host"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$StartScript = (Resolve-Path (Join-Path $ProjectRoot "scripts\start-windows-gpu.ps1")).Path
$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$StartScript`" -ProjectRoot `"$ProjectRoot`""

$action = New-ScheduledTaskAction -Execute $PowerShell -Argument $arguments -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

Write-Host "已注册 Windows 登录自启任务：$TaskName" -ForegroundColor Green
Write-Host "说明：任务在当前用户登录后启动，适合 Docker Desktop 的用户会话；不会下载模型。"
Write-Host "查看任务：Get-ScheduledTask -TaskName `"$TaskName`""

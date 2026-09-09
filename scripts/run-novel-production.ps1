[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$BaseUrl = "http://127.0.0.1:8000",
    [string]$Novel,
    [string]$ProjectId,
    [string]$RunId,
    [ValidateSet("pause", "resume", "cancel", "status")]
    [string]$Control,
    [string]$Title,
    [string]$Language = "zh-CN",
    [ValidateRange(1, 100)]
    [int]$Episodes = 1,
    [ValidateRange(30, 600)]
    [int]$EpisodeDuration = 60,
    [ValidateSet("unknown", "pending", "confirmed", "denied")]
    [string]$RightsStatus = "unknown",
    [ValidateSet("local_safe", "local_balanced", "high_quality")]
    [string]$QualityProfile,
    [string]$ImageProviderProfile,
    [string]$VideoProviderProfile,
    [ValidateSet("align", "asr")]
    [string]$SubtitleMode = "align",
    [ValidateSet("auto", "always", "off")]
    [string]$ShotKeyframeMode = "auto",
    [switch]$IncludeBGM,
    [string]$IdempotencyKey,
    [ValidateRange(0.1, 300)]
    [double]$PollInterval = 5.0,
    [ValidateRange(0, 86400)]
    [double]$TimeoutSeconds = 0.0,
    [switch]$NoWait
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$hasNovel = -not [string]::IsNullOrWhiteSpace($Novel)
$hasProject = -not [string]::IsNullOrWhiteSpace($ProjectId)
$hasRun = -not [string]::IsNullOrWhiteSpace($RunId)
$hasControl = -not [string]::IsNullOrWhiteSpace($Control)

if ($hasControl) {
    if (-not $hasProject -or -not $hasRun) {
        throw "使用 -Control 时必须同时提供 -ProjectId 和 -RunId"
    }
    if ($hasNovel) {
        throw "控制已有 Run 时不要提供 -Novel"
    }
    if ($NoWait) {
        throw "-Control 不需要与 -NoWait 一起使用"
    }
} else {
    if ($hasRun) {
        throw "-RunId 只能与 -Control 一起使用"
    }
    if ($hasNovel -eq $hasProject) {
        throw "请提供 -Novel（新建项目）或 -ProjectId（恢复已有项目）中的一个"
    }
}

Push-Location $ProjectRoot
try {
    if ($hasNovel -and -not (Test-Path -LiteralPath $Novel -PathType Leaf)) {
        throw "小说文件不存在：$Novel"
    }

    $cliPath = Join-Path $ProjectRoot "scripts\run-novel-production.py"
    if (-not (Test-Path -LiteralPath $cliPath -PathType Leaf)) {
        throw "找不到生产 CLI：$cliPath"
    }

    # Keep the Windows host profile explicit for child processes.  Provider
    # secrets remain in the caller's environment and are never printed.
    $env:AI_VIDEO_PROFILE = "windows_gpu"
    $env:AI_VIDEO_CONFIG = "config/config.windows_gpu.toml"

    $arguments = @($cliPath, "--base-url", $BaseUrl)
    if ($hasControl) {
        $arguments += @("--project-id", $ProjectId, "--run-id", $RunId, "--control", $Control)
    } elseif ($hasNovel) {
        $arguments += @("--novel", (Resolve-Path -LiteralPath $Novel).Path)
        if (-not [string]::IsNullOrWhiteSpace($Title)) {
            $arguments += @("--title", $Title)
        }
        $arguments += @(
            "--language", $Language,
            "--episodes", [string]$Episodes,
            "--episode-duration", [string]$EpisodeDuration,
            "--rights-status", $RightsStatus
        )
        if (-not [string]::IsNullOrWhiteSpace($QualityProfile)) {
            $arguments += @("--quality-profile", $QualityProfile)
        }
        if (-not [string]::IsNullOrWhiteSpace($ImageProviderProfile)) {
            $arguments += @("--image-provider-profile", $ImageProviderProfile)
        }
        if (-not [string]::IsNullOrWhiteSpace($VideoProviderProfile)) {
            $arguments += @("--video-provider-profile", $VideoProviderProfile)
        }
        $arguments += @("--subtitle-mode", $SubtitleMode, "--shot-keyframe-mode", $ShotKeyframeMode)
        if ($IncludeBGM) {
            $arguments += "--include-bgm"
        }
    } else {
        $arguments += @("--project-id", $ProjectId)
    }

    if (-not [string]::IsNullOrWhiteSpace($IdempotencyKey)) {
        $arguments += @("--idempotency-key", $IdempotencyKey)
    }
    $arguments += @("--poll-interval", [string]$PollInterval)
    if ($TimeoutSeconds -gt 0) {
        $arguments += @("--timeout-seconds", [string]$TimeoutSeconds)
    }
    if ($NoWait) {
        $arguments += "--no-wait"
    }

    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $uv) {
        & $uv.Source run python @arguments
    } else {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $python) {
            throw "找不到 uv 或 python，无法运行小说生产 CLI。"
        }
        & $python.Source @arguments
    }
    $exitCode = $LASTEXITCODE
} finally {
    Pop-Location
}

exit $exitCode

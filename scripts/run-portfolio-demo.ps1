[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$ConfigPath = "config/config.windows_gpu.toml",
    [string]$ComfyUIUrl = "http://127.0.0.1:8188",
    [string]$OllamaUrl = "http://127.0.0.1:11434",
    [ValidateSet("local_safe", "local_balanced", "high_quality")]
    [string]$QualityProfile = "local_balanced",
    [ValidateRange(1, 12)]
    [int]$Shots = 10,
    [ValidateSet(3, 4, 5)]
    [int]$ShotDuration = 5,
    [string]$OutputDir = ".tmp/portfolio-demo",
    [string]$ReferenceManifest,
    [switch]$ReuseRecentReferences,
    [switch]$Resume,
    [ValidateRange(1, 12)]
    [int]$StopAfterShot = 0,
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$resolvedConfig = Join-Path $ProjectRoot $ConfigPath
if (-not (Test-Path -LiteralPath $resolvedConfig -PathType Leaf)) {
    throw "找不到配置文件：$resolvedConfig。请用 -ConfigPath 指定与目标主机匹配的配置。"
}

function Test-Endpoint {
    param(
        [string]$Name,
        [string]$Uri
    )

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 5
        if ($response.StatusCode -lt 200 -or $response.StatusCode -ge 300) {
            throw "HTTP $($response.StatusCode)"
        }
        Write-Host "[PASS] $Name · $Uri" -ForegroundColor Green
    } catch {
        throw "[FAIL] $Name 不可用：$Uri。$($_.Exception.Message)"
    }
}

Push-Location $ProjectRoot
try {
    if (-not $SkipPreflight) {
        Write-Host "作品集 Demo 运行前检查（按内存档案，不绑定具体 GPU 型号）" -ForegroundColor Cyan
        Test-Endpoint -Name "ComfyUI" -Uri "$ComfyUIUrl/system_stats"
        Test-Endpoint -Name "Ollama" -Uri "$OllamaUrl/api/tags"
    }

    $env:AI_VIDEO_PROFILE = "windows_gpu"
    $env:AI_VIDEO_CONFIG = (Resolve-Path $resolvedConfig).Path
    $env:AI_VIDEO_VISUAL_QUALITY_PROFILE = $QualityProfile
    # The runner executes on the host, so do not inherit Docker-only endpoints
    # or artifact paths from a .env used by the API/Worker containers.
    $env:AI_VIDEO_STORAGE_PROVIDER = "local"
    $env:AI_VIDEO_STORAGE_BASE_PATH = (Join-Path $ProjectRoot ".tmp/portfolio-artifacts")
    $env:AI_VIDEO_STORAGE_URI_PREFIX = "local://"
    $env:AI_VIDEO_IMAGE_BASE_URL = $ComfyUIUrl
    $env:AI_VIDEO_VIDEO_BASE_URL = $ComfyUIUrl
    $env:AI_VIDEO_LLM_BASE_URL = "$OllamaUrl/v1"

    $arguments = @(
        "scripts/run-local-portfolio-sample.py",
        "--config", $env:AI_VIDEO_CONFIG,
        "--shots", [string]$Shots,
        "--shot-duration", [string]$ShotDuration,
        "--quality-profile", $QualityProfile,
        "--output-dir", $OutputDir
    )
    if (-not [string]::IsNullOrWhiteSpace($ReferenceManifest)) {
        $arguments += @("--reference-manifest", $ReferenceManifest)
    }
    if ($ReuseRecentReferences) {
        $arguments += "--reuse-recent-references"
    }
    if ($Resume) {
        $arguments += "--resume"
    }
    if ($StopAfterShot -gt 0) {
        $arguments += @("--stop-after-shot", [string]$StopAfterShot)
    }

    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if ($null -ne $uv) {
        & $uv.Source run python @arguments
    } else {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if ($null -eq $python) {
            throw "找不到 uv 或 python，无法运行作品集 Demo。"
        }
        & $python.Source @arguments
    }
    if ($LASTEXITCODE -ne 0) {
        throw "作品集 Demo 运行失败，退出码 $LASTEXITCODE。请查看 $OutputDir/report.json 和控制台中的结构化错误。"
    }

    $reportPath = Join-Path $ProjectRoot (Join-Path $OutputDir "report.json")
    if (Test-Path -LiteralPath $reportPath -PathType Leaf) {
        Write-Host "作品集 Demo 已完成：$reportPath" -ForegroundColor Green
        Write-Host "请先查看 report.json 的 portfolio_readiness，再人工审核角色、动作、声音、字幕和完整观感。" -ForegroundColor Yellow
    }
} finally {
    Pop-Location
}

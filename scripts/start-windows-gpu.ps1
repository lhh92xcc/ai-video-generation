[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$ComfyUIRoot = $env:COMFYUI_ROOT,
    [string]$ComfyUIPython = $env:COMFYUI_PYTHON,
    [string]$MuseTalkPython = $env:MUSETALK_PYTHON,
    [switch]$SkipOllama,
    [switch]$SkipComfyUI,
    [switch]$SkipMuseTalk,
    [switch]$RequireMuseTalk,
    [switch]$SkipBuild,
    [switch]$RunPortfolioSmoke,
    [ValidateSet(3, 4, 5)]
    [int]$SmokeShotDuration = 3,
    [ValidateSet("local_safe", "local_balanced", "high_quality")]
    [string]$SmokeQualityProfile = "local_safe",
    [string]$SmokeOutputDir = ".tmp/windows-portfolio-smoke"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$env:AI_VIDEO_PROFILE = "windows_gpu"
$env:AI_VIDEO_CONFIG = "config/config.windows_gpu.toml"

function Test-Endpoint {
    param([string]$Uri)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 3
        return ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300)
    } catch {
        return $false
    }
}

function Wait-Endpoint {
    param(
        [string]$Name,
        [string]$Uri,
        [int]$TimeoutSeconds = 60
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    while ((Get-Date) -lt $deadline) {
        if (Test-Endpoint -Uri $Uri) {
            Write-Host "$Name 已就绪" -ForegroundColor Green
            return $true
        }
        Start-Sleep -Seconds 2
    }
    Write-Warning "$Name 在 $TimeoutSeconds 秒内没有响应"
    return $false
}

function Start-BackgroundProcess {
    param(
        [string]$FilePath,
        [string[]]$Arguments,
        [string]$WorkingDirectory,
        [hashtable]$Environment = @{}
    )
    foreach ($entry in $Environment.GetEnumerator()) {
        [Environment]::SetEnvironmentVariable($entry.Key, [string]$entry.Value, "Process")
    }
    return Start-Process -FilePath $FilePath -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory -WindowStyle Minimized -PassThru
}

Write-Host "AI Video Generation / Windows GPU 主机启动器" -ForegroundColor Cyan
Write-Host "项目目录: $ProjectRoot"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "找不到 docker。请先安装并启动 Docker Desktop。"
}
& docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Desktop 未运行。请先启动 Docker Desktop，等待引擎就绪后重试。"
}

if (-not $SkipOllama) {
    if (-not (Test-Endpoint -Uri "http://127.0.0.1:11434/api/tags")) {
        if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
            throw "Ollama 未安装或不在 PATH。请安装 Ollama 并准备 qwen2.5:7b。"
        }
        Write-Host "正在启动 Ollama..."
        Start-Process -FilePath "ollama" -ArgumentList @("serve") -WindowStyle Minimized | Out-Null
        Wait-Endpoint -Name "Ollama" -Uri "http://127.0.0.1:11434/api/tags" -TimeoutSeconds 45 | Out-Null
    } else {
        Write-Host "Ollama 已在运行" -ForegroundColor Green
    }
}

if (-not $SkipComfyUI) {
    if (-not (Test-Endpoint -Uri "http://127.0.0.1:8188/system_stats")) {
        if ([string]::IsNullOrWhiteSpace($ComfyUIRoot)) {
            throw "ComfyUI 未运行。请通过 -ComfyUIRoot 指定安装目录，或设置 COMFYUI_ROOT。"
        }
        $ComfyUIRoot = (Resolve-Path $ComfyUIRoot).Path
        if ([string]::IsNullOrWhiteSpace($ComfyUIPython)) {
            $ComfyUIPython = Join-Path $ComfyUIRoot ".venv\Scripts\python.exe"
        }
        if (-not (Test-Path $ComfyUIPython)) {
            throw "找不到 ComfyUI Python: $ComfyUIPython"
        }
        Write-Host "正在启动 ComfyUI（低显存串行模式）..."
        $ComfyArguments = @(
            "main.py", "--listen", "0.0.0.0", "--port", "8188",
            "--lowvram", "--reserve-vram", "2"
        )
        Start-BackgroundProcess -FilePath $ComfyUIPython -Arguments $ComfyArguments `
            -WorkingDirectory $ComfyUIRoot | Out-Null
        Wait-Endpoint -Name "ComfyUI" -Uri "http://127.0.0.1:8188/system_stats" -TimeoutSeconds 120 | Out-Null
    } else {
        Write-Host "ComfyUI 已在运行" -ForegroundColor Green
    }
}

if (-not $SkipMuseTalk) {
    if (-not (Test-Endpoint -Uri "http://127.0.0.1:8090/healthz")) {
        if ([string]::IsNullOrWhiteSpace($MuseTalkPython)) {
            $MuseTalkPython = "python"
        }
        if ([string]::IsNullOrWhiteSpace($env:MUSETALK_WRAPPER_PATH)) {
            Write-Warning "MUSETALK_WRAPPER_PATH 未设置，将启动 bridge 但健康状态会是 degraded。"
        }
        Write-Host "正在启动 MuseTalk HTTP bridge..."
        $bridgeArguments = @("scripts/musetalk_http_runner.py")
        Start-BackgroundProcess -FilePath $MuseTalkPython -Arguments $bridgeArguments `
            -WorkingDirectory $ProjectRoot -Environment @{
                "MUSETALK_HOST" = "0.0.0.0"
                "MUSETALK_PORT" = "8090"
            } | Out-Null
        Wait-Endpoint -Name "MuseTalk bridge" -Uri "http://127.0.0.1:8090/healthz" -TimeoutSeconds 30 | Out-Null
    } else {
        Write-Host "MuseTalk bridge 已在运行" -ForegroundColor Green
    }
}

Push-Location $ProjectRoot
try {
    Write-Host "正在启动 API、Worker、前端和基础设施..."
    $composeArguments = @("compose", "up", "-d")
    if (-not $SkipBuild) {
        $composeArguments += "--build"
    }
    $composeArguments += @("frontend", "api", "worker")
    & docker @composeArguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose 启动失败，退出码 $LASTEXITCODE"
    }
} finally {
    Pop-Location
}

$checkScript = Join-Path $ProjectRoot "scripts\check-windows-gpu.ps1"
$healthArguments = @("-ProjectRoot", $ProjectRoot)
$healthArguments += "-RequireComposeMedia"
if (-not $SkipOllama) {
    $healthArguments += "-RequireOllamaModel"
}
if (-not $SkipComfyUI) {
    $healthArguments += "-RequireComfyUI"
    # A running ComfyUI endpoint is not enough to prove that the host can
    # generate media.  When the installation root is known, validate the
    # actual model files and custom-node directories as well.
    if (-not [string]::IsNullOrWhiteSpace($ComfyUIRoot)) {
        $healthArguments += @("-ComfyUIRoot", $ComfyUIRoot, "-ValidateComfyUIAssets")
    } else {
        Write-Warning "未提供 COMFYUI_ROOT，只能检查 ComfyUI API 和节点；无法检查宿主机模型文件。"
    }
}
if (-not [string]::IsNullOrWhiteSpace($ComfyUIPython)) {
    $healthArguments += @("-ComfyUIPython", $ComfyUIPython)
}
if ($RequireMuseTalk) {
    $healthArguments += "-RequireMuseTalk"
}
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $checkScript @healthArguments
$checkExitCode = $LASTEXITCODE
if ($checkExitCode -ne 0) {
    exit $checkExitCode
}

if ($RunPortfolioSmoke) {
    Push-Location $ProjectRoot
    try {
        Write-Host "正在执行一镜头真实媒体 Smoke（质量档案: $SmokeQualityProfile，时长: ${SmokeShotDuration}s）..." -ForegroundColor Cyan
        $env:AI_VIDEO_VISUAL_QUALITY_PROFILE = $SmokeQualityProfile
        # The runner executes on the Windows host, not inside the Docker
        # container. Override container-only paths/endpoints for this smoke so
        # Artifacts stay local and ComfyUI is reached through localhost.
        $env:AI_VIDEO_STORAGE_PROVIDER = "local"
        $env:AI_VIDEO_STORAGE_BASE_PATH = (Join-Path $ProjectRoot ".tmp/windows-portfolio-artifacts")
        $env:AI_VIDEO_STORAGE_URI_PREFIX = "local://"
        $env:AI_VIDEO_IMAGE_BASE_URL = "http://127.0.0.1:8188"
        $env:AI_VIDEO_VIDEO_BASE_URL = "http://127.0.0.1:8188"
        $env:AI_VIDEO_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
        $runnerArguments = @(
            "scripts/run-local-portfolio-sample.py",
            "--shots", "1",
            "--shot-duration", [string]$SmokeShotDuration,
            "--quality-profile", $SmokeQualityProfile,
            "--output-dir", $SmokeOutputDir
        )
        $uvCommand = Get-Command uv -ErrorAction SilentlyContinue
        if ($null -ne $uvCommand) {
            & $uvCommand.Source run python @runnerArguments
        } else {
            $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
            if ($null -eq $pythonCommand) {
                throw "找不到 uv 或 python，无法执行真实媒体 Smoke。"
            }
            & $pythonCommand.Source @runnerArguments
        }
        if ($LASTEXITCODE -ne 0) {
            throw "一镜头真实媒体 Smoke 失败，退出码 $LASTEXITCODE。请查看 $SmokeOutputDir/report.json 和 Worker 日志。"
        }
        Write-Host "一镜头真实媒体 Smoke 已完成：$SmokeOutputDir" -ForegroundColor Green
    } finally {
        Pop-Location
    }
}

exit 0

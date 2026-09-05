[CmdletBinding()]
param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [switch]$RequireMuseTalk
)

$ErrorActionPreference = "Continue"
$failures = [System.Collections.Generic.List[string]]::new()

function Add-Result {
    param(
        [string]$Name,
        [bool]$Ok,
        [string]$Message,
        [bool]$Required = $true
    )

    $state = if ($Ok) { "PASS" } elseif ($Required) { "FAIL" } else { "WARN" }
    $color = if ($Ok) { "Green" } elseif ($Required) { "Red" } else { "Yellow" }
    Write-Host ("[{0}] {1}: {2}" -f $state, $Name, $Message) -ForegroundColor $color
    if (-not $Ok -and $Required) {
        $failures.Add($Name)
    }
}

function Test-HttpEndpoint {
    param(
        [string]$Name,
        [string]$Uri,
        [bool]$Required = $true
    )

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 5
        Add-Result -Name $Name -Ok ($response.StatusCode -ge 200 -and $response.StatusCode -lt 300) `
            -Message ("HTTP {0}" -f $response.StatusCode) -Required $Required
        return $response
    } catch {
        Add-Result -Name $Name -Ok $false -Message $_.Exception.Message -Required $Required
        return $null
    }
}

function Test-Command {
    param(
        [string]$Name,
        [string[]]$Arguments,
        [bool]$Required = $true
    )

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Add-Result -Name $Name -Ok $false -Message "命令不存在" -Required $Required
        return $false
    }
    & $Name @Arguments *> $null
    $ok = ($LASTEXITCODE -eq 0)
    $message = if ($ok) { "可执行" } else { "退出码 $LASTEXITCODE" }
    Add-Result -Name $Name -Ok $ok -Message $message -Required $Required
    return $ok
}

Write-Host "AI Video Generation / Windows RTX 4060 Ti 8 GB 健康检查" -ForegroundColor Cyan
Write-Host "项目目录: $ProjectRoot"
Write-Host ""

Test-Command -Name "nvidia-smi" -Arguments @() -Required $true | Out-Null
Test-Command -Name "docker" -Arguments @("info") -Required $true | Out-Null
if (Get-Command docker -ErrorAction SilentlyContinue) {
    Push-Location $ProjectRoot
    try {
        & docker compose config --quiet *> $null
        $composeOk = ($LASTEXITCODE -eq 0)
        $composeMessage = if ($composeOk) { "配置有效" } else { "配置无效，请检查 .env 和 Compose 文件" }
        Add-Result -Name "docker compose config" -Ok $composeOk `
            -Message $composeMessage
    } finally {
        Pop-Location
    }
}

$apiResponse = Test-HttpEndpoint -Name "API /healthz" -Uri "http://127.0.0.1:8000/healthz" -Required $true
$operationalResponse = Test-HttpEndpoint -Name "API /api/v1/system/health" `
    -Uri "http://127.0.0.1:8000/api/v1/system/health" -Required $true

Test-HttpEndpoint -Name "Ollama" -Uri "http://127.0.0.1:11434/api/tags" -Required $false | Out-Null
Test-HttpEndpoint -Name "ComfyUI" -Uri "http://127.0.0.1:8188/system_stats" -Required $false | Out-Null
Test-HttpEndpoint -Name "MuseTalk bridge" -Uri "http://127.0.0.1:8090/healthz" -Required $RequireMuseTalk | Out-Null

if ($operationalResponse -ne $null) {
    try {
        $healthBody = $operationalResponse.Content | ConvertFrom-Json
        Write-Host ""
        Write-Host ("运行档案: {0} · 总状态: {1} · 可用磁盘: {2} GB" -f `
            $healthBody.profile, $healthBody.status, $healthBody.disk_free_gb) -ForegroundColor Cyan
        foreach ($component in $healthBody.components) {
            Write-Host ("  {0}: {1} · {2}" -f $component.name, $component.status, $component.message)
        }
        if ($healthBody.status -eq "degraded") {
            Write-Host "提示：degraded 表示某个可选或宿主机 Provider 尚未启动，不一定是 API 故障。" -ForegroundColor Yellow
        }
    } catch {
        Add-Result -Name "health JSON" -Ok $false -Message "无法解析健康检查响应" -Required $true
    }
}

Write-Host ""
if ($failures.Count -gt 0) {
    Write-Host ("健康检查失败：{0}" -f ($failures -join ", ")) -ForegroundColor Red
    exit 1
}
Write-Host "健康检查通过。" -ForegroundColor Green
exit 0

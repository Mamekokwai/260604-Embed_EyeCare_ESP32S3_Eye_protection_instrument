<#
.SYNOPSIS
    构建并预检 EyeCare 生产固件，不烧写硬件。

.DESCRIPTION
    加载 ESP-IDF 5.4.4 环境后调用安全构建脚本。
    默认先清理 build-production，避免沿用旧 bootloader 或分区表。
#>
[CmdletBinding()]
param(
    [string]$BuildDir = "build-production",
    [switch]$NoClean,
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolsDir = Split-Path -Parent $scriptDir
$projectDir = Split-Path -Parent $toolsDir
$securityBuilder = Join-Path $projectDir "tools\security\build_production.ps1"
$idfExport = Join-Path $env:USERPROFILE ".espressif\v5.4.4\esp-idf\export.ps1"

if (-not (Test-Path -LiteralPath $securityBuilder -PathType Leaf)) {
    throw "生产构建脚本不存在: $securityBuilder"
}
if (-not (Test-Path -LiteralPath $idfExport -PathType Leaf)) {
    throw "未找到 ESP-IDF 5.4.4 环境脚本: $idfExport"
}

Set-Location -LiteralPath $projectDir
. $idfExport

$buildParams = @{
    BuildDir = $BuildDir
}
if (-not $NoClean) {
    $buildParams.Clean = $true
}
if ($SkipPreflight) {
    $buildParams.SkipPreflight = $true
}

& $securityBuilder @buildParams
exit $LASTEXITCODE

<#
.SYNOPSIS
    Flash the complete EyeCare production image.

.DESCRIPTION
    Write bootloader, partition table, application, and the production fallback
    image in the storage partition in one operation.
    This script does not run idf.py flash or write eFuse.
#>
[CmdletBinding()]
param(
    [string]$Port = "",
    [int]$Baud = 0,
    [string]$BuildDir = "",
    [switch]$ConfirmProductionFlash
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$toolsDir = Split-Path -Parent $scriptDir
$projectDir = Split-Path -Parent $toolsDir
$confPath = Join-Path $scriptDir "flash_production.conf"

$configPort = ""
$configBaud = 460800
$configBuildDir = "build-production"
$configConfirm = $false
if (Test-Path -LiteralPath $confPath -PathType Leaf) {
    foreach ($rawLine in Get-Content -LiteralPath $confPath -Encoding UTF8) {
        $line = $rawLine.Trim()
        if ([string]::IsNullOrWhiteSpace($line) -or $line.StartsWith("#")) {
            continue
        }
        if ($line -match '^PORT\s*=\s*(.+)$') {
            $configPort = $matches[1].Trim().Trim('"')
        } elseif ($line -match '^BAUD\s*=\s*(\d+)$') {
            $configBaud = [int]$matches[1]
        } elseif ($line -match '^BUILD_DIR\s*=\s*(.+)$') {
            $configBuildDir = $matches[1].Trim().Trim('"')
        } elseif ($line -match '^CONFIRM_PRODUCTION_FLASH\s*=\s*(1|true|yes)$') {
            $configConfirm = $true
        }
    }
}

if ([string]::IsNullOrWhiteSpace($Port)) { $Port = $configPort }
if ($Baud -eq 0) { $Baud = $configBaud }
if ([string]::IsNullOrWhiteSpace($BuildDir)) { $BuildDir = $configBuildDir }
if ($configConfirm) { $ConfirmProductionFlash = $true }

$buildPath = Join-Path $projectDir $BuildDir
$sdkconfigPath = Join-Path $buildPath "sdkconfig.production"
$bootloader = Join-Path $buildPath "bootloader\bootloader.bin"
$partitionTable = Join-Path $buildPath "partition_table\partition-table.bin"
$application = Join-Path $buildPath "template-app.bin"
$storageImage = Join-Path $buildPath "storage.bin"
$preflight = Join-Path $projectDir "tools\security\production_preflight.py"
$idfPython = Join-Path $env:USERPROFILE ".espressif\python_env\idf5.4_py3.12_env\Scripts\python.exe"
$idfExport = Join-Path $env:USERPROFILE ".espressif\v5.4.4\esp-idf\export.ps1"

if (-not (Test-Path -LiteralPath $idfExport -PathType Leaf)) {
    throw "ESP-IDF 5.4.4 environment script not found: $idfExport"
}
Set-Location -LiteralPath $projectDir
. $idfExport

if (-not (Test-Path -LiteralPath $idfPython -PathType Leaf)) {
    throw "ESP-IDF Python environment not found: $idfPython"
}

foreach ($required in @($sdkconfigPath, $bootloader, $partitionTable, $application, $storageImage, $preflight)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) {
        throw "Production artifact missing. Run tools\windows\build_production.ps1 first: $required"
    }
}

$configText = Get-Content -LiteralPath $sdkconfigPath -Raw
foreach ($requiredConfig in @(
    "CONFIG_PARTITION_TABLE_OFFSET=0x10000",
    "CONFIG_EYECARE_PRODUCTION_LOCK=y",
    "CONFIG_SECURE_BOOT_V2_ENABLED=y",
    "CONFIG_SECURE_FLASH_ENCRYPTION_MODE_RELEASE=y",
    "CONFIG_NVS_ENCRYPTION=y"
)) {
    if ($configText -notmatch [regex]::Escape($requiredConfig)) {
        throw "Required production sdkconfig entry is missing: $requiredConfig"
    }
}

if ([string]::IsNullOrWhiteSpace($Port)) {
    $jtagPorts = @(
        Get-CimInstance Win32_PnPEntity -ErrorAction SilentlyContinue |
            Where-Object {
                $_.PNPDeviceID -match 'VID_303A&PID_1001' -and
                $_.Name -match '\(COM\d+\)'
            } |
            ForEach-Object { [regex]::Match($_.Name, '\(COM\d+\)').Value.Trim('(', ')') }
    ) | Select-Object -Unique

    if ($jtagPorts.Count -eq 1) {
        $Port = $jtagPorts[0]
    } else {
        throw "Could not uniquely identify native USB Serial-JTAG port. Use -Port COMxx. Candidates: $($jtagPorts -join ', ')"
    }
}

Write-Host "Production image flash plan:" -ForegroundColor Cyan
Write-Host "  Config: $confPath"
Write-Host "  $Port @ $Baud baud"
Write-Host "  0x000000  $bootloader"
Write-Host "  0x010000  $partitionTable"
Write-Host "  0x020000  $application"
Write-Host "  0x120000  $storageImage (SDCard.jpg fallback)"
Write-Host ""
Write-Host "The signed firmware and SDCard.jpg storage payload will be written; eFuse will not be changed." -ForegroundColor Yellow

if (-not $ConfirmProductionFlash) {
    $answer = Read-Host 'Type FLASH-PRODUCTION to continue, or press Enter to cancel'
    if ($answer -ne "FLASH-PRODUCTION") {
        Write-Host "Flash cancelled."
        exit 2
    }
}

Write-Host "[1/2] Running read-only production preflight..."
& $idfPython $preflight `
    --project $projectDir `
    --build-dir $BuildDir `
    --sdkconfig $sdkconfigPath `
    --unlock-key (Join-Path $env:USERPROFILE ".ssh\260604-Embed_EyeCare_ESP32S3_320x320") `
    --secure-boot-key (Join-Path $env:USERPROFILE ".ssh\260604-Embed_EyeCare_ESP32S3_320x320_secure_boot_rsa3072.pem")
if ($LASTEXITCODE -ne 0) {
    throw "Production preflight failed; flash aborted."
}

Write-Host "[2/2] Flashing complete production image..."
& $idfPython -m esptool `
    --chip esp32s3 `
    --port $Port `
    --baud $Baud `
    --before default_reset `
    --after hard_reset `
    --no-stub `
    write_flash `
    --flash_mode dio `
    --flash_freq 80m `
    --flash_size keep `
    0x000000 $bootloader `
    0x010000 $partitionTable `
    0x020000 $application `
    0x120000 $storageImage

if ($LASTEXITCODE -ne 0) {
    throw "Production image flash failed, exit=$LASTEXITCODE"
}

Write-Host "Production image flash completed. No eFuse operation was performed." -ForegroundColor Green

<#
.SYNOPSIS
    Build and preflight the EyeCare production image without flashing hardware.

.DESCRIPTION
    Uses a separate build directory and an ignored, generated Kconfig overlay.
    The Secure Boot RSA key and per-device unlock key remain outside the
    repository. This script never invokes `flash` and never writes eFuse.
#>
[CmdletBinding()]
param(
    [string]$BuildDir = "build-production",
    [string]$Sdkconfig = "sdkconfig.production",
    [string]$UnlockKey = (Join-Path $env:USERPROFILE ".ssh\260604-Embed_EyeCare_ESP32S3_320x320"),
    [string]$SecureBootKey = (Join-Path $env:USERPROFILE ".ssh\260604-Embed_EyeCare_ESP32S3_320x320_secure_boot_rsa3072.pem"),
    [switch]$SkipPreflight
)

$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$buildPath = Join-Path $projectRoot $BuildDir
$localDefaults = Join-Path $buildPath "sdkconfig.production.local.defaults"
$sdkconfigForBuild = Join-Path $buildPath ([IO.Path]::GetFileName($Sdkconfig))
Set-Location -LiteralPath $projectRoot

if (-not (Test-Path -LiteralPath $UnlockKey -PathType Leaf)) {
    throw "Unlock private key not found: $UnlockKey"
}
if (-not (Test-Path -LiteralPath $SecureBootKey -PathType Leaf)) {
    throw "Secure Boot RSA private key not found: $SecureBootKey"
}

New-Item -ItemType Directory -Path $buildPath -Force | Out-Null
$secureBootKeyForKconfig = $SecureBootKey.Replace("\", "/").Replace('"', '\"')
@"
CONFIG_EFUSE_CUSTOM_TABLE=y
CONFIG_EFUSE_CUSTOM_TABLE_FILENAME="main/esp_efuse_custom_table.csv"
CONFIG_PARTITION_TABLE_OFFSET=0x10000
CONFIG_EYECARE_PRODUCTION_LOCK=y
CONFIG_SECURE_BOOT=y
CONFIG_SECURE_BOOT_V2_ENABLED=y
CONFIG_SECURE_SIGNED_APPS_RSA_SCHEME=y
CONFIG_SECURE_BOOT_BUILD_SIGNED_BINARIES=y
CONFIG_SECURE_BOOT_SIGNING_KEY="$secureBootKeyForKconfig"
CONFIG_SECURE_FLASH_ENC_ENABLED=y
CONFIG_SECURE_FLASH_ENCRYPTION_AES256=y
CONFIG_SECURE_FLASH_ENCRYPTION_MODE_RELEASE=y
CONFIG_SECURE_ENABLE_SECURE_ROM_DL_MODE=y
CONFIG_NVS_ENCRYPTION=y
"@ | ForEach-Object {
    [IO.File]::WriteAllText(
        $localDefaults,
        $_,
        ([System.Text.UTF8Encoding]::new($false))
    )
}

$defaults = @(
    (Join-Path $projectRoot "sdkconfig.defaults"),
    (Join-Path $projectRoot "sdkconfig.production.defaults"),
    $localDefaults
) | ForEach-Object { $_.Replace("\", "/") }
$defaultsValue = $defaults -join ";"

Write-Host "[1/2] Building production image in $buildPath"
& idf.py -B $BuildDir -D "SDKCONFIG=$sdkconfigForBuild" -D "SDKCONFIG_DEFAULTS=$defaultsValue" build
if ($LASTEXITCODE -ne 0) {
    throw "Production build failed with exit code $LASTEXITCODE"
}

if (-not $SkipPreflight) {
    Write-Host "[2/2] Running read-only production preflight"
    & python (Join-Path $projectRoot "tools/security/production_preflight.py") `
        --project $projectRoot `
        --build-dir $BuildDir `
        --sdkconfig $sdkconfigForBuild `
        --unlock-key $UnlockKey `
        --secure-boot-key $SecureBootKey
    if ($LASTEXITCODE -ne 0) {
        throw "Production preflight failed with exit code $LASTEXITCODE"
    }
}

Write-Host "Production image is ready. No device was flashed and no eFuse was changed."

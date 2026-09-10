param(
    [string]$BlenderVersion = "",
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Source = (Resolve-Path (Join-Path $Root "addon")).Path.TrimEnd('\')
$BlenderRoot = Join-Path $env:APPDATA "Blender Foundation\Blender"

if (-not (Test-Path -LiteralPath $BlenderRoot)) {
    throw "Blender user config directory was not found: $BlenderRoot"
}

if (-not $BlenderVersion) {
    $versions = Get-ChildItem -LiteralPath $BlenderRoot -Directory |
        Where-Object { $_.Name -match '^\d+\.\d+$' } |
        Sort-Object { [version]$_.Name } -Descending
    if (-not $versions) {
        throw "No Blender version directory was found under: $BlenderRoot"
    }
    $BlenderVersion = $versions[0].Name
}

if ($BlenderVersion -notmatch '^\d+\.\d+$') {
    throw "BlenderVersion must look like 5.2, 4.4, etc."
}

$ExtensionRoot = Join-Path $BlenderRoot "$BlenderVersion\extensions\user_default"
$Target = Join-Path $ExtensionRoot "p1782_92"

# Never keep backups inside extensions\user_default. Blender scans children in
# that directory as extension packages, so names such as
# p1782_92.package-backup can be interpreted as importable extension modules.
$BackupRoot = Join-Path $BlenderRoot "$BlenderVersion\1782-92-backups"
$Backup = Join-Path $BackupRoot "p1782_92"
$LegacyBackup = "$Target.package-backup"

if (Get-Process blender -ErrorAction SilentlyContinue) {
    throw "Blender is running. Save your work, close Blender, then run this script again."
}

function Test-ReparsePoint([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return $false
    }
    $item = Get-Item -LiteralPath $Path -Force
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Get-LinkTarget([string]$Path) {
    $item = Get-Item -LiteralPath $Path -Force
    $raw = @($item.Target) | Select-Object -First 1
    if (-not $raw) {
        return $null
    }
    try {
        return [IO.Path]::GetFullPath([string]$raw).TrimEnd('\')
    } catch {
        return [string]$raw
    }
}

function Move-LegacyBackupOutOfExtensionRoot {
    if (-not (Test-Path -LiteralPath $LegacyBackup)) {
        return
    }

    if (Test-Path -LiteralPath $Backup) {
        throw "Both legacy and safe add-on backups exist. Resolve these before continuing:`n  Legacy: $LegacyBackup`n  Safe  : $Backup"
    }

    New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
    Move-Item -LiteralPath $LegacyBackup -Destination $Backup
    Write-Host "Migrated legacy packaged add-on backup out of Blender's extension scan path:"
    Write-Host "  $LegacyBackup"
    Write-Host "  -> $Backup"
}

# v0.1.4 and earlier versions of this helper stored the packaged backup next
# to the live extension. Migrate it before any early return so an already-
# linked checkout also repairs the broken backup layout.
Move-LegacyBackupOutOfExtensionRoot

if ($Remove) {
    if (Test-Path -LiteralPath $Target) {
        if (-not (Test-ReparsePoint $Target)) {
            throw "Refusing to remove $Target because it is not a development link."
        }
        Remove-Item -LiteralPath $Target -Force
        Write-Host "Removed development link: $Target"
    }

    if (Test-Path -LiteralPath $Backup) {
        Move-Item -LiteralPath $Backup -Destination $Target
        Write-Host "Restored packaged add-on backup: $Target"
        try {
            if ((Test-Path -LiteralPath $BackupRoot) -and -not (Get-ChildItem -LiteralPath $BackupRoot -Force | Select-Object -First 1)) {
                Remove-Item -LiteralPath $BackupRoot -Force
            }
        } catch {
            # Backup-root cleanup is cosmetic; never fail restoration because of it.
        }
    } else {
        Write-Host "No packaged add-on backup existed."
    }
    return
}

New-Item -ItemType Directory -Force -Path $ExtensionRoot | Out-Null

if (Test-Path -LiteralPath $Target) {
    if (Test-ReparsePoint $Target) {
        $current = Get-LinkTarget $Target
        if ($current -and $current.TrimEnd('\') -ieq $Source) {
            Write-Host "Already linked: $Target -> $Source"
            Write-Host "Future updates: git pull, then restart Blender."
            return
        }
        throw "A different link already exists at $Target -> $current"
    }

    if (Test-Path -LiteralPath $Backup) {
        throw "Backup already exists at $Backup. Resolve it before installing the development link."
    }
    New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
    Move-Item -LiteralPath $Target -Destination $Backup
    Write-Host "Backed up packaged add-on outside Blender's extension scan path: $Backup"
}

New-Item -ItemType Junction -Path $Target -Target $Source | Out-Null

Write-Host "Linked 1782-92 development add-on:"
Write-Host "  Blender: $Target"
Write-Host "  Source : $Source"
Write-Host ""
Write-Host "From now on, git pull updates the add-on files in place."
Write-Host "Restart Blender after pulling so Python modules reload."
Write-Host "To restore the packaged install:"
Write-Host "  .\scripts\link-addon-dev.ps1 -BlenderVersion $BlenderVersion -Remove"

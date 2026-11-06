$ErrorActionPreference = 'Stop'

# Remove only the per-user Setotower helper installation. Downloads are kept.
$installRoot = Join-Path $env:LOCALAPPDATA 'SetotowerDownloader'
$helper = Join-Path $installRoot 'helper.exe'

if (Test-Path -LiteralPath $helper -PathType Leaf) {
    Start-Process -FilePath $helper -ArgumentList '--uninstall' -Wait -WindowStyle Hidden
}

if (Test-Path -LiteralPath $installRoot) {
    Remove-Item -LiteralPath $installRoot -Recurse -Force
}

Write-Output "Setotower helper removed. Downloads in $env:USERPROFILE\Downloads\Setotower were preserved."

<#
    Builds the zip that goes to someone else's PC.

        powershell -ExecutionPolicy Bypass -File packaging\make_zip.ps1

    What comes out is dist\RigDeck-Setup-<date>.zip: extract it anywhere, double-click
    one file, and the whole thing installs with no internet and no build tools. That
    means bundling a Python runtime, the vJoy installer, the telemetry plugin and the
    already-built map exporter -- see packaging\NOTICE.txt for who wrote what.

    What deliberately does NOT go in:

      android\      the signing key lives there. Nothing in that folder is needed to
                    run Rig Deck, so the simplest way not to ship a private key is not
                    to ship the folder.
      web\maps\     ~70 MB of road network derived from the game's own archives. Not
                    ours to hand on; the installer exports it from his copy instead.
      server\config.json   the local port and unit settings, written at runtime.
      tools\ts-map, tools\refs, tools\nupkg   build-time only; the exporter is shipped
                    already built.
#>

[CmdletBinding()]
param(
    [string]$Version = (Get-Date -Format 'yyyy-MM-dd')
)

$ErrorActionPreference = 'Stop'

$root    = Split-Path $PSScriptRoot -Parent
$stage   = Join-Path $root 'dist\stage'
$folder  = Join-Path $stage 'RigDeck'
$runtime = Join-Path $root 'tools\python-embed\runtime'
$plugin  = Join-Path $env:TEMP 'rigdeck-plugin'

function Say($text)  { Write-Host "  $text" -ForegroundColor Cyan }
function Item($text) { Write-Host "      $text" -ForegroundColor DarkGray }

Write-Host ""
Say "Rig Deck -- building the distribution zip"

# -- things that must be there before we start -----------------------------------
$needed = @{
    'the Python runtime (packaging\fetch_runtime.ps1)' = Join-Path $runtime 'pythonw.exe'
    'the built map exporter (export_maps.ps1)'         = Join-Path $root 'tools\mapexport\bin\MapExport.exe'
    'the vJoy installer'                               = Join-Path $root 'tools\vJoySetup-2.2.2.0-Win10-Win11.exe'
    'the tablet app (build_apk.ps1)'                   = Join-Path $root 'dist\RigDeck.apk'
    'the icon (install_app.ps1)'                       = Join-Path $root 'RigDeck.ico'
}
$missing = $needed.GetEnumerator() | Where-Object { -not (Test-Path $_.Value) }
if ($missing) {
    Write-Host ""
    foreach ($m in $missing) { Write-Host "  missing: $($m.Key)" -ForegroundColor Red; Write-Host "           $($m.Value)" -ForegroundColor DarkGray }
    Write-Host ""
    throw "cannot build the zip until those exist"
}

# The plugin zip is whatever install_plugin.ps1 last downloaded. Fetch one if this
# machine has never run it.
$pluginZip = Get-ChildItem $plugin -Filter '*.zip' -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $pluginZip) {
    Say "fetching the telemetry plugin release"
    New-Item -ItemType Directory -Force $plugin | Out-Null
    $release = Invoke-RestMethod -UseBasicParsing -Headers @{ 'User-Agent' = 'rigdeck' } `
        -Uri 'https://api.github.com/repos/RenCloud/scs-sdk-plugin/releases/latest'
    $asset = $release.assets | Where-Object { $_.name -like '*.zip' } | Select-Object -First 1
    $out = Join-Path $plugin $asset.name
    Invoke-WebRequest -UseBasicParsing -Uri $asset.browser_download_url -OutFile $out
    $pluginZip = Get-Item $out
}

# -- staging ---------------------------------------------------------------------
Say "staging"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $folder | Out-Null

function Stage-Tree($from, $to, $exclude) {
    $source = Join-Path $root $from
    $target = Join-Path $folder $to
    New-Item -ItemType Directory -Force $target | Out-Null
    Copy-Item "$source\*" $target -Recurse -Force
    foreach ($pattern in $exclude) {
        Get-ChildItem $target -Recurse -Force -Filter $pattern -ErrorAction SilentlyContinue |
            Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
    }
    Item $to
}

Stage-Tree 'server' 'server' @('__pycache__', 'config.json', '*.pyc')
Stage-Tree 'web'    'web'    @('maps', 'test_qr.js')
Stage-Tree 'tools\mapexport\bin' 'tools\mapexport\bin' @('*.pdb', '*.xml')

New-Item -ItemType Directory -Force (Join-Path $folder 'runtime') | Out-Null
Copy-Item "$runtime\*" (Join-Path $folder 'runtime') -Recurse -Force
Item 'runtime (Python, bundled)'

# The embeddable build only searches beside its own exe, so the server folder is named
# here rather than left for the installer to patch in.
$pth = Get-ChildItem (Join-Path $folder 'runtime') -Filter 'python*._pth' | Select-Object -First 1
$lines = Get-Content $pth.FullName
if ($lines -notcontains '..\server') { ($lines + '..\server') | Set-Content $pth.FullName -Encoding ASCII }

New-Item -ItemType Directory -Force (Join-Path $folder 'setup') | Out-Null
foreach ($file in @('setup.ps1', 'bind.ps1', 'uninstall.ps1', 'vjoycheck.py', 'vjoydevice.ps1')) {
    Copy-Item (Join-Path $PSScriptRoot $file) (Join-Path $folder 'setup') -Force
}
Copy-Item (Join-Path $root 'tools\vJoySetup-2.2.2.0-Win10-Win11.exe') (Join-Path $folder 'setup') -Force
# PowerShell variables are case-insensitive, so this cannot be called $version without
# quietly renaming the zip after the plugin instead of the release.
$pluginVersion = ($pluginZip.BaseName -replace '[^0-9_]', '' -replace '^_+|_+$', '') -replace '_', '.'
Copy-Item $pluginZip.FullName (Join-Path $folder "setup\scs-sdk-plugin-$pluginVersion.zip") -Force
Item "setup (installer, vJoy, plugin $pluginVersion)"

Copy-Item (Join-Path $PSScriptRoot 'bat\*.bat') $folder -Force
Copy-Item (Join-Path $PSScriptRoot 'UPUTE.txt') $folder -Force
Copy-Item (Join-Path $PSScriptRoot 'NOTICE.txt') $folder -Force
# The MIT licence, under the name NOTICE.txt points at.
Copy-Item (Join-Path $root 'LICENSE') (Join-Path $folder 'LICENSE.txt') -Force
foreach ($file in @('README.md', 'RigDeck.ico', 'export_maps.ps1', 'install_plugin.ps1', 'run.ps1')) {
    Copy-Item (Join-Path $root $file) $folder -Force
}
# Served from the panel, so the tablet can install it by scanning a code rather than
# being plugged into anything.
Copy-Item (Join-Path $root 'dist\RigDeck.apk') (Join-Path $folder 'web') -Force
Item 'the tablet app, served at /RigDeck.apk'

# This repository is kept with Unix line endings, and cmd.exe does not read a .bat that
# way: a multi-line `if (...)` block with bare newlines in it is parsed wrongly and the
# elevation step silently falls apart. Everything textual goes out CRLF.
foreach ($file in (Get-ChildItem $folder -Recurse -File -Include '*.bat', '*.ps1', '*.txt', '*.md')) {
    $text = [System.IO.File]::ReadAllText($file.FullName)
    $fixed = ($text -replace "`r`n", "`n") -replace "`n", "`r`n"
    if ($fixed -ne $text) { [System.IO.File]::WriteAllText($file.FullName, $fixed) }
}
Item 'line endings normalised for Windows'

# UPUTE.txt is read in Notepad by someone who did not ask for an encoding argument.
$text = Get-Content (Join-Path $folder 'UPUTE.txt') -Raw -Encoding UTF8
[System.IO.File]::WriteAllText((Join-Path $folder 'UPUTE.txt'), $text, (New-Object System.Text.UTF8Encoding $true))

# -- nothing private got in ------------------------------------------------------
Say "checking what is in there"
$banned = @('keystore', '*.jks', '*.keystore', 'local.properties', 'config.json', '*.pyc')
$found = foreach ($pattern in $banned) {
    Get-ChildItem $folder -Recurse -Force -Filter $pattern -ErrorAction SilentlyContinue
}
if ($found) {
    $found | ForEach-Object { Write-Host "  MUST NOT SHIP: $($_.FullName)" -ForegroundColor Red }
    throw "the staging folder has something private in it"
}
Item 'no keys, no local config'

# -- the zip ---------------------------------------------------------------------
Say "compressing"
$zipPath = Join-Path $root "dist\RigDeck-Setup-$Version.zip"
& (Join-Path $folder 'runtime\python.exe') (Join-Path $PSScriptRoot 'zipdir.py') $folder $zipPath
if ($LASTEXITCODE -ne 0) { throw "the zip was not written" }

Remove-Item -Recurse -Force $stage

Write-Host ""
Write-Host "  Ready to send:" -ForegroundColor Green
Write-Host "    $zipPath"
Write-Host ""

<#
    Rig Deck, installed in one go.

    This is the script behind "1 - INSTALIRAJ RIG DECK.bat". It is written for a PC that
    has never seen any of this before: no Python, no vJoy, no telemetry plugin, no map.
    Everything it needs is in the folder beside it, so the only thing it goes online for
    is nothing at all.

    It never adds anything to Windows startup. Rig Deck is started from its own icon when
    there is a drive to do, and quit from the tray afterwards.

        setup.ps1               the lot
        setup.ps1 -SkipMaps     everything except the map export (which takes minutes)
#>

[CmdletBinding()]
param(
    [switch]$SkipMaps
)

$ErrorActionPreference = 'Stop'

$here    = $PSScriptRoot
$root    = Split-Path $here -Parent
$runtime = Join-Path $root 'runtime'
$python  = Join-Path $runtime 'python.exe'
$pythonw = Join-Path $runtime 'pythonw.exe'
$server  = Join-Path $root 'server'
$port    = 8384

$script:steps = @()

function Note($text)  { Write-Host "      $text" -ForegroundColor DarkGray }
function Good($text)  { Write-Host "      $text" -ForegroundColor Green }
function Warn($text)  { Write-Host "      $text" -ForegroundColor Yellow }
function Head($n, $text) {
    Write-Host ""
    Write-Host ("  {0}. {1}" -f $n, $text) -ForegroundColor Cyan
}
function Remember($text) { $script:steps += $text }

Write-Host ""
Write-Host "  RIG DECK -- installation" -ForegroundColor White
Write-Host "  $root"

# -- 0. is this a real folder? ---------------------------------------------------
# Double-clicking a .bat straight out of Explorer's zip preview runs it from a
# read-only temp folder that Windows deletes underneath it. Everything appears to
# work and then the icon on the desktop points at nothing.
if ($root -like "*\AppData\Local\Temp\*" -or $root -like "*\Temp\Temp*") {
    Write-Host ""
    Write-Host "  This is running from inside the zip file, not from a real folder." -ForegroundColor Red
    Write-Host "  Extract the whole RigDeck folder somewhere first -- C:\RigDeck or the" -ForegroundColor Red
    Write-Host "  desktop is fine -- and run it again from there." -ForegroundColor Red
    Write-Host ""
    return
}

# -- 1. the Python runtime -------------------------------------------------------
Head 1 "Python runtime"
if (-not (Test-Path $python)) { throw "runtime\python.exe is missing -- the zip was not extracted completely." }

# The embeddable build only looks for modules beside its own exe, so the server folder
# has to be named in the path file or `import rigdeck` finds nothing.
$pth = Get-ChildItem $runtime -Filter 'python*._pth' | Select-Object -First 1
if ($pth) {
    $lines = Get-Content $pth.FullName
    if ($lines -notcontains '..\server') {
        ($lines + '..\server') | Set-Content $pth.FullName -Encoding ASCII
        Note "pointed the runtime at the server folder"
    }
}
$check = & $python -c "import rigdeck, sys; print(sys.version.split()[0])" 2>&1
if ($LASTEXITCODE -ne 0) { throw "the bundled Python cannot load Rig Deck:`n$check" }
Good "Python $check, bundled -- nothing to install"

# -- 2. the telemetry plugin -----------------------------------------------------
Head 2 "Telemetry plugin for the game"
$zip = Get-ChildItem $here -Filter 'scs-sdk-plugin-*.zip' | Select-Object -First 1
if (-not $zip) {
    Warn "the plugin zip is missing from the setup folder -- skipped"
    Remember "Install the telemetry plugin: powershell -ExecutionPolicy Bypass -File install_plugin.ps1"
} else {
    # A PC with no game on it yet is a perfectly ordinary thing to install onto, so
    # nothing in here is allowed to end the run.
    try {
        $global:LASTEXITCODE = 0
        & (Join-Path $root 'install_plugin.ps1') -Zip $zip.FullName
        if ($LASTEXITCODE -eq 1) {
            Warn "no ETS2 or ATS installation was found on this PC"
            Remember "Install ETS2 or ATS, then run: powershell -ExecutionPolicy Bypass -File install_plugin.ps1"
        }
    } catch {
        Warn "the plugin could not be installed ($($_.Exception.Message))"
        Remember "Install the telemetry plugin: powershell -ExecutionPolicy Bypass -File install_plugin.ps1"
    }
}

# -- 3. vJoy ---------------------------------------------------------------------
Head 3 "vJoy -- the virtual controller the panel presses"
$vjoyDll = @(
    "$env:ProgramFiles\vJoy\x64\vJoyInterface.dll",
    "${env:ProgramFiles(x86)}\vJoy\x64\vJoyInterface.dll"
) | Where-Object { Test-Path $_ } | Select-Object -First 1

if (-not $vjoyDll) {
    $installer = Get-ChildItem $here -Filter 'vJoySetup*.exe' | Select-Object -First 1
    if (-not $installer) { throw "the vJoy installer is missing from the setup folder." }
    Write-Host ""
    Write-Host "      vJoy is not installed. The installer opens now -- click through it" -ForegroundColor Yellow
    Write-Host "      and say yes to the driver." -ForegroundColor Yellow
    Write-Host ""
    Write-Host "      Two things to know:" -ForegroundColor Yellow
    Write-Host "        * this build is signed, so Memory Integrity can stay ON" -ForegroundColor Yellow
    Write-Host "        * at the end it may look stuck -- there is a restart question" -ForegroundColor Yellow
    Write-Host "          hiding behind the window. Alt+Tab to it and answer." -ForegroundColor Yellow
    Write-Host ""
    Start-Process -FilePath $installer.FullName -Wait
    $vjoyDll = @(
        "$env:ProgramFiles\vJoy\x64\vJoyInterface.dll",
        "${env:ProgramFiles(x86)}\vJoy\x64\vJoyInterface.dll"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1
}

if (-not $vjoyDll) {
    Warn "vJoy still is not there -- the panel will show information but no button will work"
    Remember "Install vJoy from setup\vJoySetup-2.2.2.0-Win10-Win11.exe, then run '2 - POVEZI TIPKE U IGRI.bat'"
} else {
    Good "vJoy driver present"

    # Device 1 has to have at least 32 buttons; a fresh vJoy gives it 8, and pressing a
    # button that does not exist fails silently -- the panel would light up and the truck
    # would ignore it.
    $state = & $python (Join-Path $here 'vjoycheck.py') 2>&1
    Note $state
    if ("$state" -match 'buttons=(\d+)') { $buttons = [int]$matches[1] } else { $buttons = 0 }

    if ($buttons -lt 32) {
        $config = Join-Path (Split-Path (Split-Path $vjoyDll -Parent) -Parent) 'x64\vJoyConfig.exe'
        if (Test-Path $config) {
            Note "configuring vJoy device 1 with 64 buttons"
            # The axes are not used, but a stick with none of them is an odd device for
            # DirectInput to enumerate, so it gets the usual three.
            & $config 1 -f -b 64 -a X Y Z | Out-Null
            Start-Sleep -Milliseconds 1500
            $state = & $python (Join-Path $here 'vjoycheck.py') 2>&1
            Note $state
        } else {
            Warn "vJoyConfig.exe not found -- open vJoyConf and give device 1 sixty-four buttons"
        }
    }
}

# -- 3b. the two tasks that switch the vJoy device on and off --------------------
# vjoy.sys reads two characters past the end of its own device name and bugchecks the
# machine when that read crosses a page boundary -- three times in nine days here, always
# at vjoy.sys+0x6093, always while a game was enumerating controllers.  So the device is
# kept switched off and Rig Deck turns it on only while the panel is running.
#
# Switching a PnP device needs administrator rights, which the panel has not got.  These
# two tasks have them, and a task started with schtasks /run raises no UAC prompt -- that
# is the whole point of routing through them.
Head "3b" "Switching vJoy on only when the panel needs it"
$switch = Join-Path $here 'vjoydevice.ps1'
if (-not (Test-Path $switch)) {
    Warn "vjoydevice.ps1 is missing -- vJoy will stay on all the time"
} else {
    try {
        $psExe = (Get-Command powershell.exe).Source
        foreach ($t in @(@{Name='RigDeckVJoyOn'; Action='enable'},
                         @{Name='RigDeckVJoyOff'; Action='disable'})) {
            $act = New-ScheduledTaskAction -Execute $psExe -Argument (
                '-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "{0}" -Action {1}' `
                    -f $switch, $t.Action)
            # S4U, not Interactive: an "interactive only" task is silently skipped when
            # nobody is logged on at the console, and then the panel would sit waiting for
            # a device that never comes back. S4U runs either way, and RunLevel Highest
            # still gives it the rights, because the account is an administrator.
            $pri = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) `
                -LogonType S4U -RunLevel Highest
            $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Minutes 2)
            Register-ScheduledTask -TaskName $t.Name -Action $act -Principal $pri `
                -Settings $set -Description 'Rig Deck: vJoy device on/off' -Force | Out-Null
        }
        Good "registered RigDeckVJoyOn and RigDeckVJoyOff"

        # Off by default.  Nothing needs it until the panel starts, and every hour it
        # spends switched on is an hour the bug can fire.
        & $psExe -NoProfile -ExecutionPolicy Bypass -File $switch -Action disable | ForEach-Object { Note $_ }
    } catch {
        Warn "could not register the vJoy tasks ($($_.Exception.Message)) -- vJoy will stay on all the time"
    }
}

# -- 4. the firewall -------------------------------------------------------------
Head 4 "Letting the tablet reach this PC"
try {
    $name = 'Rig Deck panel'
    Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -DisplayName $name -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $port -Profile Private,Domain -Program $pythonw | Out-Null
    Good "port $port opened on private networks only"
} catch {
    Warn "could not add the firewall rule ($($_.Exception.Message))"
    Warn "Windows will ask the first time the panel starts -- tick 'Private networks'"
}

# -- 5. shortcuts ----------------------------------------------------------------
Head 5 "Desktop and Start menu"
$icon = Join-Path $root 'RigDeck.ico'
if (-not (Test-Path $icon)) {
    & $python (Join-Path $server 'rigdeck_tray.py') --write-ico $icon | Out-Null
}
$shell = New-Object -ComObject WScript.Shell
foreach ($link in @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Rig Deck.lnk'),
    (Join-Path ([Environment]::GetFolderPath('CommonPrograms')) 'Rig Deck.lnk')
)) {
    try {
        $sc = $shell.CreateShortcut($link)
        $sc.TargetPath = $pythonw
        $sc.Arguments = "`"$(Join-Path $server 'rigdeck_tray.py')`""
        $sc.WorkingDirectory = $server
        $sc.IconLocation = "$icon,0"
        $sc.Description = 'Rig Deck -- dashboard and control panel for ETS2 and ATS'
        $sc.Save()
        Good $link
    } catch {
        Warn "could not write $link"
    }
}
Note "nothing was added to Windows startup, and nothing will be"

# -- 6. the map ------------------------------------------------------------------
Head 6 "The map for the tablet's GPS"
if ($SkipMaps) {
    Warn "skipped on request -- run export_maps.ps1 later, or use the tray menu"
    Remember "Export the map: right-click the Rig Deck tray icon -> Export the map from the game files"
} else {
    Note "read straight out of your own game files, mods included. A few minutes."
    Write-Host ""
    try {
        & (Join-Path $root 'export_maps.ps1')
    } catch {
        Warn "the export did not finish ($($_.Exception.Message))"
        Remember "Export the map: right-click the Rig Deck tray icon -> Export the map from the game files"
    }
}

# -- 7. the bindings -------------------------------------------------------------
Head 7 "Binding the panel's buttons in the game"
$bound = $false
try {
    $global:LASTEXITCODE = 0
    & (Join-Path $here 'bind.ps1') -Quiet
    $bound = ($LASTEXITCODE -eq 0)
} catch {
    Warn $_.Exception.Message
}
if ($bound) {
    Good "all thirty-two buttons written into the profile"
} else {
    Warn "the bindings could not be written yet -- which is normal on a new PC"
    Remember "Start the game once, open Options -> Controls and pick vJoy Device as a controller, quit the game, then run '2 - POVEZI TIPKE U IGRI.bat'"
}

# -- done ------------------------------------------------------------------------
Write-Host ""
Write-Host "  ------------------------------------------------------------------" -ForegroundColor DarkGray
Write-Host ""
Write-Host "  Rig Deck is installed." -ForegroundColor Green
Write-Host ""
if ($script:steps.Count) {
    Write-Host "  Still to do:" -ForegroundColor Yellow
    $n = 1
    foreach ($step in $script:steps) { Write-Host "    $n) $step" -ForegroundColor Yellow; $n++ }
    Write-Host ""
}
Write-Host "  To drive:"
Write-Host "    * open Rig Deck from the desktop -- it lives in the tray, by the clock"
Write-Host "    * double-click that tray icon: two QR codes appear"
Write-Host "    * scan the lower one with the tablet to install the app (same Wi-Fi as this PC)"
Write-Host "    * start the game, open the app -- it finds this PC by itself"
Write-Host ""
Write-Host "  The whole thing is written out in UPUTE.txt beside this folder."
Write-Host ""

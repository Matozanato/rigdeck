<#
    Unhooks Rig Deck from Windows: the shortcuts and the firewall rule.

    It deliberately leaves alone the three things it did not create on its own terms --
    vJoy, the telemetry plugin in the game folder, and the game's own controls.sii --
    and says how to undo each of them by hand. Deleting this folder finishes the job.
#>

$ErrorActionPreference = 'Stop'

$root = Split-Path $PSScriptRoot -Parent

Write-Host ""
Write-Host "  RIG DECK -- removal" -ForegroundColor White
Write-Host ""

foreach ($link in @(
    (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Rig Deck.lnk'),
    (Join-Path ([Environment]::GetFolderPath('CommonPrograms')) 'Rig Deck.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'Rig Deck.lnk')
)) {
    if (Test-Path $link) { Remove-Item $link -Force; Write-Host "      removed $link" }
}

try {
    $rule = Get-NetFirewallRule -DisplayName 'Rig Deck panel' -ErrorAction SilentlyContinue
    if ($rule) { $rule | Remove-NetFirewallRule; Write-Host "      removed the firewall rule" }
} catch {
    Write-Host "      could not remove the firewall rule -- do it in Windows Defender Firewall" -ForegroundColor Yellow
}

# The two elevated tasks that switch the vJoy device on and off go with us, and the
# device is switched back on on the way out -- leaving someone's controller disabled
# after an uninstall would be a nasty surprise.
$switch = Join-Path $PSScriptRoot 'vjoydevice.ps1'
if (Test-Path $switch) {
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $switch -Action enable |
            ForEach-Object { Write-Host "      $_" }
    } catch {
        Write-Host "      could not switch vJoy back on -- do it in Device Manager" -ForegroundColor Yellow
    }
}
foreach ($t in 'RigDeckVJoyOn', 'RigDeckVJoyOff') {
    if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) {
        Unregister-ScheduledTask -TaskName $t -Confirm:$false
        Write-Host "      removed the $t task"
    }
}

# Nothing here writes this, and nothing ever did, but a startup entry is exactly the sort
# of thing people expect an uninstaller to sweep up, so it is checked.
$run = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
if ((Get-ItemProperty $run -ErrorAction SilentlyContinue).RigDeck) {
    Remove-ItemProperty $run -Name RigDeck
    Write-Host "      removed a startup entry"
}

Write-Host ""
Write-Host "  Rig Deck is unhooked. Delete this folder and it is gone."
Write-Host ""
Write-Host "  Left in place on purpose:" -ForegroundColor DarkGray
Write-Host "    * vJoy -- uninstall it from Settings -> Apps if nothing else uses it" -ForegroundColor DarkGray
Write-Host "    * the telemetry plugin -- <game>\bin\win_x64\plugins\scs-telemetry.dll" -ForegroundColor DarkGray
Write-Host "    * your bindings -- restore controls.sii.rigdeck-backup in the profile folder" -ForegroundColor DarkGray
Write-Host ""

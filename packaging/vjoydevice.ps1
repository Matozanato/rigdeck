<#
Turns the vJoy device on and off.

vJoy's driver has a bug that costs the whole machine.  When anything enumerates the
device, vjoy.sys parses its own device name to recover the device number: it skips
eight characters ("Device_") plus one more, then reads the next two -- with no check
that the name is that long.  Its own key is called "Device01", which is exactly eight
characters, so it reads past the end of the string.  Most of the time that lands in the
same page and nobody notices; when the allocation happens to finish on a page boundary
the next page is not mapped and Windows bugchecks 0x3B in vjoy.sys+0x6093.  Measured
three times in nine days on this machine, always at that same offset, always while a
game was enumerating controllers.

So the device does not stay switched on.  Rig Deck turns it on when it starts and off
when it stops, and every other program on the machine simply never sees it.

Enabling and disabling a PnP device needs administrator rights, which the panel does
not have, so setup.ps1 registers two scheduled tasks that run this script elevated:
RigDeckVJoyOn and RigDeckVJoyOff.  The panel triggers those.

    powershell -ExecutionPolicy Bypass -File vjoydevice.ps1 -Action enable|disable|status
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('enable', 'disable', 'status')]
    [string]$Action
)

# The root device.  The "vJoy Driver" node and the "HID-compliant game controller" that
# DirectInput actually sees are its children, so this one switch covers all three.
$InstanceId = 'ROOT\HIDCLASS\0000'

function Get-VJoyDevice {
    Get-PnpDevice -InstanceId $InstanceId -ErrorAction SilentlyContinue
}

$dev = Get-VJoyDevice
if (-not $dev) {
    Write-Output "vjoy: device $InstanceId not found (vJoy not installed?)"
    exit 2
}

switch ($Action) {
    'status' {
        Write-Output "vjoy: $($dev.Status) ($($dev.FriendlyName))"
        if ($dev.Status -eq 'OK') { exit 0 } else { exit 1 }
    }
    'enable' {
        if ($dev.Status -eq 'OK') { Write-Output "vjoy: already on"; exit 0 }
        Enable-PnpDevice -InstanceId $InstanceId -Confirm:$false -ErrorAction Stop
        # The driver needs a moment before vJoyInterface can acquire the device; the
        # caller polls as well, this is just so a manual run reports the truth.
        Start-Sleep -Milliseconds 800
        $dev = Get-VJoyDevice
        Write-Output "vjoy: enabled -> $($dev.Status)"
        if ($dev.Status -eq 'OK') { exit 0 } else { exit 1 }
    }
    'disable' {
        if ($dev.Status -ne 'OK') { Write-Output "vjoy: already off"; exit 0 }
        Disable-PnpDevice -InstanceId $InstanceId -Confirm:$false -ErrorAction Stop
        Start-Sleep -Milliseconds 400
        $dev = Get-VJoyDevice
        Write-Output "vjoy: disabled -> $($dev.Status)"
        exit 0
    }
}

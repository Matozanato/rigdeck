@echo off
rem Rig Deck turns the vJoy device on when a panel connects and off again shortly after
rem the last one leaves, because its driver takes the whole machine down now and then
rem when something enumerates it. This is the manual switch, for when you want vJoy for
rem something else.
rem
rem No administrator rights needed: it only triggers the two tasks the installer set up.
title Rig Deck - vJoy on/off
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s = (Get-PnpDevice -InstanceId 'ROOT\HIDCLASS\0000' -ErrorAction SilentlyContinue).Status; if (-not $s) { 'vJoy is not installed.' } else { 'vJoy is now: ' + $s }"

echo.
echo   1 - turn vJoy ON   (keep it on for another program)
echo   2 - turn vJoy OFF  (the safe state; Rig Deck turns it on by itself)
echo   3 - do nothing
echo.
set /p pick=  choose 1, 2 or 3:

if "%pick%"=="1" schtasks /run /tn RigDeckVJoyOn
if "%pick%"=="2" schtasks /run /tn RigDeckVJoyOff

if "%pick%"=="3" goto done
timeout /t 3 >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "'vJoy is now: ' + (Get-PnpDevice -InstanceId 'ROOT\HIDCLASS\0000' -ErrorAction SilentlyContinue).Status"

:done
echo.
pause

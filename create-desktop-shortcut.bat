@echo off
rem Creates a "My Watch Log" shortcut on your Desktop.
rem Run this once (double-click it). Then use the Desktop icon from then on.
rem
rem The shortcut runs Watchlog.vbs through wscript.exe rather than start.bat,
rem so launching the app never flashes a console window.
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$lnk = $ws.CreateShortcut([IO.Path]::Combine($ws.SpecialFolders('Desktop'), 'My Watch Log.lnk'));" ^
  "$lnk.TargetPath = 'wscript.exe';" ^
  "$lnk.Arguments = [char]34 + '%~dp0Watchlog.vbs' + [char]34;" ^
  "$lnk.WorkingDirectory = '%~dp0';" ^
  "$lnk.IconLocation = '%~dp0static\favicon.ico';" ^
  "$lnk.Description = 'Launch My Watch Log (YouTube Video Organizer)';" ^
  "$lnk.Save();"

if errorlevel 1 (
    echo.
    echo Could not create the shortcut.
    pause
    exit /b 1
)

echo.
echo Done - "My Watch Log" is now on your Desktop.
echo Double-click it any time to open the app in its own window.
echo.
pause

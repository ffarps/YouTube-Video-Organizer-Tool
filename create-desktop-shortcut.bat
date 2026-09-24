@echo off
rem Creates a "Peneira" shortcut on your Desktop.
rem Run this once (double-click it). Then use the Desktop icon from then on.
rem
rem The shortcut runs Watchlog.vbs through wscript.exe rather than start.bat,
rem so launching the app never flashes a console window.
setlocal
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ws = New-Object -ComObject WScript.Shell;" ^
  "$desk = $ws.SpecialFolders('Desktop');" ^
  "$old = [IO.Path]::Combine($desk, 'My Watch Log.lnk');" ^
  "$new = [IO.Path]::Combine($desk, 'Peneira.lnk');" ^
  "if ((Test-Path $old) -and -not (Test-Path $new)) { Move-Item $old $new };" ^
  "$lnk = $ws.CreateShortcut($new);" ^
  "$lnk.TargetPath = 'wscript.exe';" ^
  "$lnk.Arguments = [char]34 + '%~dp0Watchlog.vbs' + [char]34;" ^
  "$lnk.WorkingDirectory = '%~dp0';" ^
  "$lnk.IconLocation = '%~dp0static\favicon.ico';" ^
  "$lnk.Description = 'Launch Peneira (YouTube Video Organizer)';" ^
  "$lnk.Save();"

rem Windows caches a shortcut's icon by the icon file's path, and that path never
rem changes, so a new favicon.ico keeps showing the old picture until the cache
rem is told to refresh.
ie4uinit.exe -show >nul 2>&1

if errorlevel 1 (
    echo.
    echo Could not create the shortcut.
    pause
    exit /b 1
)

echo.
echo Done - "Peneira" is now on your Desktop.
echo Double-click it any time to open the app in its own window.
echo.
pause

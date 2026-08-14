' My Watch Log - silent launcher.
'
' Double-click this (or the Desktop shortcut) to open the app in its own
' window. Nothing else appears: no console, not even a flash of one. start.bat
' does the same job, but Windows always shows a console for a batch file,
' while wscript.exe never does - which is the whole reason this file exists.
Option Explicit

Dim sh, fso, base, py, pyw, rc

Set sh  = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base

If fso.FileExists(base & "\.venv\Scripts\pythonw.exe") Then
    py  = """" & base & "\.venv\Scripts\python.exe"""
    pyw = """" & base & "\.venv\Scripts\pythonw.exe"""
Else
    py  = "python.exe"
    pyw = "pythonw.exe"
End If

' Are the dependencies installed? Ask hidden (0) and wait for the answer (True).
On Error Resume Next
rc = sh.Run(py & " -c ""import uvicorn, fastapi, yt_dlp, webview""", 0, True)
If Err.Number <> 0 Then rc = 1 : Err.Clear   ' no Python on PATH at all
On Error GoTo 0

If rc <> 0 Then
    ' First run. Install in a visible window: it takes minutes, and a silent
    ' launcher that appears to do nothing for that long is worse than a console.
    rc = sh.Run("""" & base & "\start.bat"" --install-only", 1, True)
    If rc <> 0 Then
        MsgBox "Could not install the dependencies." & vbCrLf & vbCrLf & _
               "Run start.bat directly to see what went wrong.", _
               16, "My Watch Log"
        WScript.Quit 1
    End If
End If

' pythonw.exe = the same interpreter without a console, so there is nothing to
' hide here and style 1 (normal) is required: Windows hands the style down to
' the child's own windows, and style 0 launches the app perfectly - serving,
' invisible, killable only from Task Manager. Don't wait for it either; this
' script should be gone by the time the window appears.
sh.Run pyw & " -m app.desktop", 1, False

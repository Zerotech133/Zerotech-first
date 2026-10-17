' Hidden launcher for scheduled tasks (2026-10-16)
' Usage: wscript.exe _run_hidden.vbs <command> [args...]
' Runs the command with window style 0 (completely hidden), no console flash.
Set args = WScript.Arguments
If args.Count < 1 Then WScript.Quit 1
cmd = """" & args(0) & """"
extra = ""
For i = 1 To args.Count - 1
  a = args(i)
  If InStr(a, " ") > 0 Then a = """" & a & """"
  extra = extra & " " & a
Next
CreateObject("WScript.Shell").Run cmd & extra, 0, False

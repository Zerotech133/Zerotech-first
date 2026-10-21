@echo off
REM ============================================================
REM FeishuBotWatchdog — SYSTEM 计划任务注册
REM 用法：右键 → 以管理员身份运行
REM ============================================================
setlocal

set "TN=FeishuBotWatchdog"
set "WRAP=C:\aios\portal\run_bot_watchdog.ps1"

echo === FeishuBotWatchdog SYSTEM 注册 ===
echo.

if not exist "%WRAP%" (
    echo ABORT: %WRAP% 不存在
    pause & exit /b 1
)

schtasks /delete /tn "%TN%" /f >nul 2>&1

echo [TRY] schtasks /ru SYSTEM /rl HIGHEST ...
schtasks /create /tn "%TN%" /sc minute /mo 5 ^
    /ru SYSTEM /rl HIGHEST ^
    /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%WRAP%\"" ^
    /f 2>&1 | findstr /i "SUCCESS ERROR"

if errorlevel 1 (
    echo.
    echo [FALLBACK] SYSTEM 失败，降级当前用户
    schtasks /create /tn "%TN%" /sc minute /mo 5 ^
        /tr "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"%WRAP%\"" ^
        /f 2>&1 | findstr /i "SUCCESS ERROR"
)

echo.
echo === 验证 ===
schtasks /query /tn "%TN%" /fo LIST /v 2>&1 | findstr /c:"TaskName" /c:"Status" /c:"Run As" /c:"Next Run"
pause

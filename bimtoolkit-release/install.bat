@echo off
chcp 65001 >nul 2>&1
setlocal EnableDelayedExpansion
title BIMToolkit 一键安装器 v0.1

echo.
echo  +--------------------------------------------------+
echo  ^|     BIMToolkit 一键安装器 v0.1                    ^|
echo  ^|     BIM 工具箱 + AI 智建 + Revit MCP Agent 接口   ^|
echo  +--------------------------------------------------+
echo.

set "SCRIPT_DIR=%~dp0"
set "EXT_NAME=BIMToolkit.extension"
set "EXT_SRC=%SCRIPT_DIR%%EXT_NAME%"
set "PYREVIT_EXT_DIR=%APPDATA%\pyRevit\Extensions"
set "EXT_DEST=%PYREVIT_EXT_DIR%\%EXT_NAME%"
set "APP_DIR=%APPDATA%\BIMToolkit"
set "GW_DIR=%APP_DIR%\revit-mcp"
set "PY_DIR=%APP_DIR%\python"

set "PASS=0"
set "FAIL=0"

:: =========================================================================
:: 1. 检查 Revit 与 pyRevit
:: =========================================================================
echo [1/5] 检查 Revit / pyRevit ...

set "REVIT_OK="
for %%d in (
    "C:\Program Files\Autodesk\Revit 2019\Revit.exe"
    "D:\Autodesk\Revit 2019\Revit.exe"
    "C:\Program Files\Autodesk\Revit 2020\Revit.exe"
    "C:\Program Files\Autodesk\Revit 2021\Revit.exe"
    "C:\Program Files\Autodesk\Revit 2022\Revit.exe"
    "C:\Program Files\Autodesk\Revit 2023\Revit.exe"
    "C:\Program Files\Autodesk\Revit 2024\Revit.exe"
) do (
    if exist %%d set "REVIT_OK=%%~d"
)

if defined REVIT_OK (
    echo   √ Revit 已安装: %REVIT_OK%
    echo     ^(注意: 本版本在 Revit 2019 上实测，其他版本未经充分验证^)
    set /a PASS+=1
) else (
    echo   × 未找到 Revit 2019-2024，请先安装 Revit
    set /a FAIL+=1
)

set "PYREVIT_EXE="
for %%d in (
    "%LOCALAPPDATA%\pyRevit\pyRevit.exe"
    "%ProgramFiles%\pyRevit\pyRevit.exe"
    "%ProgramFiles(x86)%\pyRevit\pyRevit.exe"
    "%LOCALAPPDATA%\Programs\pyRevit\pyRevit.exe"
) do (
    if exist %%d set "PYREVIT_EXE=%%~d"
)

if defined PYREVIT_EXE (
    echo   √ pyRevit 已安装: %PYREVIT_EXE%
    set /a PASS+=1
) else (
    echo   × pyRevit 未安装
    echo     请手动安装 pyRevit ^(本扩展在 pyRevit 6.x + Revit 2019 实测^):
    echo     https://github.com/pyrevitlabs/pyRevit/releases
    echo     安装后重新运行本安装器。
    set /a FAIL+=1
)

:: =========================================================================
:: 2. 部署扩展到 pyRevit Extensions 目录
:: =========================================================================
echo.
echo [2/5] 部署 BIMToolkit 扩展 ...

if not exist "%PYREVIT_EXT_DIR%" mkdir "%PYREVIT_EXT_DIR%" 2>nul

REM ---------------------------------------------------------------------
REM NOTE: the /XD /XF list below MUST stay identical to RUNTIME_EXCLUDE_*
REM       in pyrevit-bim-panel\deploy.py (the dev-machine deploy channel).
REM
REM       Do NOT reintroduce "rmdir /s /q %EXT_DEST%" before the copy.
REM       2026-10-01 fix: that line wiped runtime state which the bridge
REM       needs but which does not exist in the source tree --
REM       bridge.token / mcp_audit.jsonl / mcp_crash.log / logs_archive.
REM       /MIR already removes stale files, so the rmdir was redundant.
REM ---------------------------------------------------------------------
robocopy "%EXT_SRC%" "%EXT_DEST%" /MIR /XD __pycache__ logs_archive /XF bridge.token mcp_audit.jsonl mcp_bridge.log mcp_crash.log *.bak-* *.bak_* /NFL /NDL /NJH /NJS /nc /ns /np >nul 2>&1

if exist "%EXT_DEST%\extension.json" (
    echo   √ 扩展已部署到: %EXT_DEST%
    set /a PASS+=1
) else (
    echo   × 扩展部署失败
    set /a FAIL+=1
)

:: =========================================================================
:: 3. 部署 revit-mcp 网关 + 确保 CPython3
:: =========================================================================
echo.
echo [3/5] 部署 revit-mcp 网关 + 检查 Python3 ...

if not exist "%GW_DIR%" mkdir "%GW_DIR%" 2>nul
copy /y "%SCRIPT_DIR%revit-mcp\revit_mcp_gateway.py" "%GW_DIR%\" >nul 2>&1
copy /y "%SCRIPT_DIR%revit-mcp\README.md" "%GW_DIR%\" >nul 2>&1

if exist "%GW_DIR%\revit_mcp_gateway.py" (
    echo   √ 网关已部署到: %GW_DIR%
    set /a PASS+=1
) else (
    echo   × 网关部署失败
    set /a FAIL+=1
)

:: 找一个可用的 CPython3：PATH -> py launcher -> 下载嵌入式包
set "PY3="
for /f "delims=" %%p in ('where python 2^>nul') do (
    if not defined PY3 (
        "%%p" -c "import sys; sys.exit(0 if sys.version_info[0]>=3 else 1)" 2>nul
        if !errorlevel! equ 0 set "PY3=%%p"
    )
)
if not defined PY3 (
    py -3 -c "pass" >nul 2>&1
    if !errorlevel! equ 0 (
        for /f "delims=" %%p in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PY3=%%p"
    )
)

if not defined PY3 (
    echo   → 系统无 Python3，下载嵌入式 Python 3.11 ...
    if not exist "%PY_DIR%" mkdir "%PY_DIR%" 2>nul
    set "PYEMBED_ZIP=%TEMP%\python-embed-bimtoolkit.zip"
    set "URL1=https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
    set "URL2=https://registry.npmmirror.com/-/binary/python/3.11.9/python-3.11.9-embed-amd64.zip"
    powershell -Command "& { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -Uri '!URL1!' -OutFile '!PYEMBED_ZIP!' -UseBasicParsing -TimeoutSec 60 } catch { Invoke-WebRequest -Uri '!URL2!' -OutFile '!PYEMBED_ZIP!' -UseBasicParsing } }" 2>nul
    if exist "!PYEMBED_ZIP!" (
        powershell -Command "Expand-Archive -Path '!PYEMBED_ZIP!' -DestinationPath '%PY_DIR%' -Force" 2>nul
        del "!PYEMBED_ZIP!" 2>nul
    )
    if exist "%PY_DIR%\python.exe" set "PY3=%PY_DIR%\python.exe"
)

if defined PY3 (
    echo   √ CPython3: %PY3%
    "%PY3%" --version 2>nul
    set /a PASS+=1
) else (
    echo   × 无法获得 CPython3（网关需要，Revit 内按钮不受影响）
    echo     请手动安装 Python 3.8+ 后重新运行本安装器
    set /a FAIL+=1
)

:: =========================================================================
:: 4. 写入 WorkBuddy MCP 配置（合并，不覆盖其他连接器）
:: =========================================================================
echo.
echo [4/5] 配置 WorkBuddy MCP 连接器 ...

if defined PY3 (
    "%PY3%" "%SCRIPT_DIR%configure_mcp.py" --python "%PY3%" --gateway "%GW_DIR%\revit_mcp_gateway.py"
    if !errorlevel! equ 0 (
        set /a PASS+=1
    ) else (
        echo   × 配置写入失败，请按上面提示手动配置
        set /a FAIL+=1
    )
) else (
    echo   → 跳过（无 CPython3）
    echo     手动配置: 编辑 %%USERPROFILE%%\.workbuddy\mcp.json,
    echo     在 mcpServers 中加入:
    echo       "revit": { "command": "python", "args": ["%GW_DIR%\revit_mcp_gateway.py"] }
    set /a FAIL+=1
)

:: =========================================================================
:: 5. 汇总
:: =========================================================================
echo.
echo  +--------------------------------------------------+
echo  ^|                 安装结果                          ^|
echo  +--------------------------------------------------+
echo    通过: %PASS% 项    失败: %FAIL% 项
echo.

if %FAIL% gtr 0 (
    echo  [!] 有失败项时的影响：
    echo      - pyRevit 未装 → Revit 里看不到按钮（必须装）
    echo      - Python3 未装 → MCP Agent 接口不可用，Revit 内按钮正常
    echo.
)

echo  ----------------------------------------------------
echo  下一步：
echo    1. 重启 Revit，工具栏出现「BIMToolkit」选项卡
echo    2. 点「MCP Bridge」按钮启动桥（日志显示 v7.5 ready）
echo    3. WorkBuddy 连接器管理页 Trust revit 连接器
echo    4. 对 WorkBuddy 说: 在 Revit 里建一面墙
echo  ----------------------------------------------------
echo.

pause
endlocal

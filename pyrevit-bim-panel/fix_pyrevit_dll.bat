@echo off
setlocal
REM fix_pyrevit_dll.bat - 一键恢复 pyRevit 缺失的 Xceed.Wpf.AvalonDock.dll
REM 适用：Revit 2019 + pyRevit 6.5.5 安装包打包缺陷
REM 所需版本：v2.0.19.10  PublicKeyToken=3e4669d2f30244f4  (410112 字节)

set "LOG=%~dp0fix_pyrevit_dll.log"
echo [%DATE% %TIME%] 开始 fix_pyrevit_dll > "%LOG%"

REM 非管理员：请求提权，等子进程跑完，再把日志打出来避免闪退。
NET SESSION >nul 2>&1
if %errorLevel% neq 0 (
    echo 需要管理员权限，正在请求提权（请点"是"）...
    echo 若窗口自动关闭，请查看同目录下的 fix_pyrevit_dll.log
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '/child' -Verb RunAs -Wait"
    echo.
    echo ===== 运行日志 =====
    type "%LOG%"
    echo.
    echo 若上方没有 [ALL OK]，说明提权被取消或复制失败；请右键本文件"以管理员身份运行"重试。
    pause
    exit /b
)

set "SRC=%~dp0tools\Xceed.Wpf.AvalonDock.dll"
set "PYROOT=C:\Program Files\pyRevit-Master"

if not exist "%SRC%" (
    echo [ERROR] 找不到源文件：%SRC% >> "%LOG%"
    echo [ERROR] 找不到源文件：%SRC%
    echo 请确认 tools\Xceed.Wpf.AvalonDock.dll 已随仓库一并检出。
    if not "%~1"=="/child" pause
    exit /b 1
)

if not exist "%PYROOT%\bin" (
    echo [ERROR] 找不到 pyRevit 安装目录：%PYROOT%\bin >> "%LOG%"
    echo [ERROR] 找不到 pyRevit 安装目录：%PYROOT%\bin
    echo 请确认 pyRevit 已安装，或修改本脚本里的 PYROOT 路径。
    if not "%~1"=="/child" pause
    exit /b 1
)

REM pyRevitLoader 的真实加载上下文在 bin\netfx\engines\ 下的引擎目录，
REM 因此把 DLL 同时复制到这些位置，确保装配绑定探测路径一定能命中。
set COPY_ERR=
for %%D in (
    "bin"
    "bin\netfx\engines"
    "bin\netfx\engines\IPY2712PR"
    "bin\netfx\engines\IPY342"
) do (
    if not exist "%PYROOT%\%%~D" (
        echo [WARN] 目录不存在，跳过：%PYROOT%\%%~D >> "%LOG%"
        echo [WARN] 目录不存在，跳过：%PYROOT%\%%~D
    ) else (
        copy /Y "%SRC%" "%PYROOT%\%%~D\Xceed.Wpf.AvalonDock.dll" >nul
        if errorlevel 1 (
            echo [ERROR] 复制到 %PYROOT%\%%~D 失败，请确认已获管理员权限。 >> "%LOG%"
            echo [ERROR] 复制到 %PYROOT%\%%~D 失败，请确认已获管理员权限。
            set COPY_ERR=1
        ) else (
            echo [OK] 已复制到 %PYROOT%\%%~D >> "%LOG%"
            echo [OK] 已复制到 %PYROOT%\%%~D
        )
    )
)

if defined COPY_ERR (
    echo [FAIL] 部分复制失败 >> "%LOG%"
    if not "%~1"=="/child" pause
    exit /b 1
)

echo. >> "%LOG%"
echo [ALL OK] 已恢复 Xceed.Wpf.AvalonDock.dll (v2.0.19.10) 到 pyRevit 各探测目录。 >> "%LOG%"
echo [ALL OK] 已恢复 Xceed.Wpf.AvalonDock.dll (v2.0.19.10) 到 pyRevit 各探测目录。
echo 关闭本窗口后重启 Revit 即可正常加载。 >> "%LOG%"
echo 关闭本窗口后重启 Revit 即可正常加载。
if not "%~1"=="/child" pause
endlocal

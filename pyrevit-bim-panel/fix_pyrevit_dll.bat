@echo off
setlocal enabledelayedexpansion
REM fix_pyrevit_dll.bat - 检测（并可选恢复）pyRevit 缺失的 Xceed.Wpf.AvalonDock.dll
REM 适用：Revit 2019 + pyRevit 6.5.5 安装包偶发丢件
REM 正确版本：AssemblyVersion 2.0.19.10 / PublicKeyToken 3e4669d2f30244f4 / 410112 字节
REM
REM 本仓库不内置这个 dll。它是 Xceed 的第三方程序集，随仓库公开分发必须把它许可原文一起带上，
REM 而那份条款在开发机上取不到（缘由与处置见 tools\README.md）。所以脚本改成两步：先只读检测；
REM 确认缺失后你自己取一份正确版本，作为参数交给脚本，脚本核对版本号再复制到 pyRevit 的
REM 各个引擎探测目录。
REM
REM 用法：
REM   fix_pyrevit_dll.bat                          仅检测
REM   fix_pyrevit_dll.bat "<dll 完整路径>"          检测 + 核版本 + 复制（写不动才请求提权）
REM   fix_pyrevit_dll.bat "<dll 完整路径>" /dry     同上，但只打印会复制到哪，不写入
REM   fix_pyrevit_dll.bat "<dll 完整路径>" /force   跳过版本核对（不推荐）
REM 环境变量 BIMTOOLKIT_PYROOT 可覆盖 pyRevit 根目录（自检/多版本共存时用）
REM 退出码：0 无需修复或已修复 / 1 出错或版本不符 / 2 确认缺失但没给 dll

set "LOG=%~dp0fix_pyrevit_dll.log"
set "PYROOT=%BIMTOOLKIT_PYROOT%"
if "%PYROOT%"=="" set "PYROOT=C:\Program Files\pyRevit-Master"
set "DLLNAME=Xceed.Wpf.AvalonDock.dll"
set "WANTVER=2.0.19.10"
set "DIRS=bin bin\netfx\engines bin\netfx\engines\IPY2712PR bin\netfx\engines\IPY342"

set "IS_CHILD=0"
set "SRC="
set "MODE="
if /I "%~1"=="/child" (
    set "IS_CHILD=1"
    set "SRC=%~2"
    set "MODE=%~3"
) else (
    set "SRC=%~1"
    set "MODE=%~2"
)

if "%IS_CHILD%"=="1" (
    echo [%DATE% %TIME%] child run >> "%LOG%"
) else (
    echo [%DATE% %TIME%] fix_pyrevit_dll > "%LOG%"
)

REM ------------------------------------------------------------ 检测（只读）
echo ===== 检测 %PYROOT% =====
set "PRESENT="
set "MISSING="
for %%D in (%DIRS%) do call :probe "%%D"
echo   在位:  !PRESENT!
echo   缺失:  !MISSING!
echo   期望版本: %WANTVER%
echo 在位:!PRESENT! 缺失:!MISSING! >> "%LOG%"

if not defined MISSING (
    echo.
    echo [ALL OK] 所有探测目录都已带 %DLLNAME%，无需修复。
    echo 若 Revit 仍报错，缺的不是这个程序集，处置见 tools\README.md。
    if "%IS_CHILD%"=="0" pause
    exit /b 0
)

echo.
echo [缺失] 需要一份 %WANTVER% 的 %DLLNAME%。
if defined SRC goto :verify
echo 本仓库不提供这个文件，从下面任一处取：
echo   1^) 另一台同版本机器的 C:\Program Files\pyRevit-Master\bin\%DLLNAME%
echo   2^) 重装 pyRevit 6.5.5，官方安装包会把它放进 bin\
echo   3^) 别处的同名 dll —— 必须先核版本。Revit 2019 自带的那份是 2.0.19.4，不是这一版
echo 取到之后运行： fix_pyrevit_dll.bat "<那份 dll 的完整路径>"
if "%IS_CHILD%"=="0" pause
exit /b 2

REM ------------------------------------------------------------ 校验来源文件
:verify
if not exist "%SRC%" (
    echo [ERROR] 找不到你给的 dll：%SRC%
    if "%IS_CHILD%"=="0" pause
    exit /b 1
)
set "GOTVER=ERR"
for /f "usebackq delims=" %%V in (`powershell -NoProfile -Command "try { ([Reflection.Assembly]::ReflectionOnlyLoadFrom('%SRC%')).GetName().Version.ToString() } catch { 'ERR' }"`) do set "GOTVER=%%V"
echo   你给的这份版本: %GOTVER%
echo 来源:%SRC% 版本:%GOTVER% >> "%LOG%"
if /I "%GOTVER%"=="%WANTVER%" goto :apply
if /I "%MODE%"=="/force" (
    echo [WARN] 版本不符（%GOTVER% 不等于 %WANTVER%），但 /force 已指定，继续。
    goto :apply
)
echo [ERROR] 版本不符：需要 %WANTVER%，你给的是 %GOTVER%。
echo         同名不同构建会让 pyRevit 加载到错的那个。确认无误要强行使用再加 /force。
if "%IS_CHILD%"=="0" pause
exit /b 1

REM ------------------------------------------------------------ 复制（写不动才提权）
:apply
if /I "%MODE%"=="/dry" echo ===== /dry：只打印目标，不写入 =====
set "COPY_ERR="
set "NEED_ADMIN="
for %%D in (%DIRS%) do call :copydir "%%D"

if defined NEED_ADMIN (
    echo 写不进 %PYROOT%，正在请求提权（请点"是"）...
    echo 若窗口自动关闭，请查看同目录下的 fix_pyrevit_dll.log
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -ArgumentList '/child \"%SRC%\"' -Verb RunAs -Wait"
    echo.
    echo ===== 运行日志 =====
    type "%LOG%"
    echo.
    echo 若上方没有 [ALL OK]，说明提权被取消或复制失败；请右键本文件"以管理员身份运行"重试。
    if "%IS_CHILD%"=="0" pause
    exit /b 1
)

if defined COPY_ERR (
    echo [FAIL] 部分目录复制失败 >> "%LOG%"
    echo [FAIL] 部分目录复制失败
    if "%IS_CHILD%"=="0" pause
    exit /b 1
)
echo.
echo [ALL OK] 处理完成（%GOTVER% 的 %DLLNAME%）。 >> "%LOG%"
echo [ALL OK] 处理完成（%GOTVER% 的 %DLLNAME%）。
if /I not "%MODE%"=="/dry" echo 关闭本窗口后重启 Revit 即可正常加载。
if "%IS_CHILD%"=="0" pause
exit /b 0

REM ------------------------------------------------------------ 子过程
:probe
set "D=%~1"
if not exist "%PYROOT%\%D%\" (
    echo [WARN] 目录不存在，跳过：%D%
    goto :eof
)
if exist "%PYROOT%\%D%\%DLLNAME%" (
    set "PRESENT=!PRESENT! [%D%]"
) else (
    set "MISSING=!MISSING! [%D%]"
)
goto :eof

:copydir
REM 只补缺失的目录，已在位的原文件一律不动
set "D=%~1"
echo !MISSING! | findstr /I /C:"[%D%]" >nul || goto :eof
if /I "%MODE%"=="/dry" (
    echo [DRY] 会复制到：%PYROOT%\%D%
    goto :eof
)
copy /Y "%SRC%" "%PYROOT%\%D%\%DLLNAME%" >nul
if not errorlevel 1 (
    echo [OK] 已复制到 %PYROOT%\%D%
    echo [OK] %PYROOT%\%D% >> "%LOG%"
    goto :eof
)
NET SESSION >nul 2>&1
if errorlevel 1 (
    echo [SKIP] 没有管理员权限，写不进 %D%，准备提权重试
    set "NEED_ADMIN=1"
    goto :eof
)
echo [ERROR] 复制到 %PYROOT%\%D% 失败（当前已是管理员）
set "COPY_ERR=1"
goto :eof

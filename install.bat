@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

:: ClaudeCursorX installer for Windows
:: Installs MCP Servers, Skills, Rules, and Subagents into a target project's .cursor/ directory.

:: ── 解析参数 ──────────────────────────────────────────────────────────────────

set "SCRIPT_DIR=%~dp0"
:: 去掉末尾反斜杠
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "TARGET_DIR=%CD%"
set "MODE=copy"
set "INSTALL_MCP=true"
set "INSTALL_SKILLS=true"
set "INSTALL_RULES=true"
set "INSTALL_AGENTS=true"
set "INSTALL_DEPS=true"

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="--link"        ( set "MODE=link"           & shift & goto :parse_args )
if /i "%~1"=="--copy"        ( set "MODE=copy"           & shift & goto :parse_args )
if /i "%~1"=="--mcp-only"    (
    set "INSTALL_SKILLS=false" & set "INSTALL_RULES=false" & set "INSTALL_AGENTS=false"
    shift & goto :parse_args
)
if /i "%~1"=="--skills-only" (
    set "INSTALL_MCP=false" & set "INSTALL_RULES=false" & set "INSTALL_AGENTS=false"
    shift & goto :parse_args
)
if /i "%~1"=="--rules-only"  (
    set "INSTALL_MCP=false" & set "INSTALL_SKILLS=false" & set "INSTALL_AGENTS=false"
    shift & goto :parse_args
)
if /i "%~1"=="--agents-only" (
    set "INSTALL_MCP=false" & set "INSTALL_SKILLS=false" & set "INSTALL_RULES=false"
    shift & goto :parse_args
)
if /i "%~1"=="--no-deps"     ( set "INSTALL_DEPS=false"  & shift & goto :parse_args )
if /i "%~1"=="-h"            ( goto :usage )
if /i "%~1"=="--help"        ( goto :usage )
:: 第一个不以 -- 开头的参数视为 TARGET_DIR
if not "%~1:~0,2%"=="--"     ( set "TARGET_DIR=%~f1"     & shift & goto :parse_args )
echo [ERROR] Unknown option: %~1
goto :usage

:usage
echo.
echo Usage: install.bat [OPTIONS] [TARGET_PROJECT_DIR]
echo.
echo Install ClaudeCursorX into a Cursor project.
echo.
echo Arguments:
echo   TARGET_PROJECT_DIR   Target project directory (default: current directory)
echo.
echo Options:
echo   --link               Use symlinks instead of copying (requires Admin rights)
echo   --copy               Copy files (default, standalone)
echo   --mcp-only           Only install MCP Servers
echo   --skills-only        Only install Skills
echo   --rules-only         Only install Rules
echo   --agents-only        Only install Subagents
echo   --no-deps            Skip Python dependency installation
echo   -h, --help           Show this help message
echo.
echo Examples:
echo   install.bat C:\my-project
echo   install.bat --mcp-only C:\my-project
echo   install.bat --link C:\my-project    (requires Administrator)
echo.
goto :eof

:args_done

:: ── 验证目标目录 ──────────────────────────────────────────────────────────────

if not exist "%TARGET_DIR%" (
    echo [ERROR] Target directory does not exist: %TARGET_DIR%
    exit /b 1
)

echo ========================================
echo   ClaudeCursorX installer
echo ========================================
echo   Source:  %SCRIPT_DIR%
echo   Target:  %TARGET_DIR%
echo   Mode:    %MODE%
echo ========================================
echo.

:: --link 模式在 Windows 需要管理员权限或开发者模式
if /i "%MODE%"=="link" (
    echo [WARN] Symlink mode requires Administrator rights or Developer Mode enabled.
    echo        Falling back to copy mode if mklink fails.
    echo.
)

:: ── 辅助函数（通过 goto 模拟） ───────────────────────────────────────────────

goto :main

:: install_file src dst
:install_file
    set "_src=%~1"
    set "_dst=%~2"
    :: 确保目标父目录存在
    for %%D in ("%_dst%") do (
        if not exist "%%~dpD" mkdir "%%~dpD" 2>nul
    )
    if /i "%MODE%"=="link" (
        :: 删除已有同名文件/目录
        if exist "%_dst%" ( del /q "%_dst%" 2>nul || rmdir /s /q "%_dst%" 2>nul )
        mklink "%_dst%" "%_src%" >nul 2>&1
        if errorlevel 1 (
            :: 降级为复制
            copy /y "%_src%" "%_dst%" >nul
            echo   COPY  %_dst%  (symlink failed, copied instead)
        ) else (
            echo   LINK  %_dst% -^> %_src%
        )
    ) else (
        copy /y "%_src%" "%_dst%" >nul
        echo   COPY  %_dst%
    )
    goto :eof

:: install_dir_link src_dir dst_dir — 对目录创建符号链接或递归复制
:install_dir_item
    set "_src=%~1"
    set "_dst=%~2"
    if /i "%MODE%"=="link" (
        if exist "%_dst%" ( rmdir /s /q "%_dst%" 2>nul )
        mklink /d "%_dst%" "%_src%" >nul 2>&1
        if errorlevel 1 (
            xcopy /e /i /q /y "%_src%" "%_dst%\" >nul
            echo   COPY  %_dst%  (dir symlink failed, copied instead)
        ) else (
            echo   LINK  %_dst% -^> %_src%
        )
    ) else (
        xcopy /e /i /q /y "%_src%" "%_dst%\" >nul
        echo   COPY  %_dst%
    )
    goto :eof

:main

:: ── MCP Servers ───────────────────────────────────────────────────────────────

if /i "%INSTALL_MCP%"=="true" (
    echo Installing MCP Servers...

    for /d %%S in ("%SCRIPT_DIR%\mcp-servers\*") do (
        set "SERVER_NAME=%%~nxS"
        set "DST_SERVER=%TARGET_DIR%\.cursor\mcp-servers\!SERVER_NAME!"
        if not exist "!DST_SERVER!" mkdir "!DST_SERVER!"
        for %%F in ("%%S\*.py") do (
            if exist "%%F" (
                call :install_file "%%F" "!DST_SERVER!\%%~nxF"
            )
        )
    )

    :: 生成 mcp.json
    set "MCP_JSON=%TARGET_DIR%\.cursor\mcp.json"
    if exist "!MCP_JSON!" (
        echo   SKIP  !MCP_JSON! (already exists, not overwriting)
        echo         Compare with: %SCRIPT_DIR%\templates\mcp.json
    ) else (
        copy /y "%SCRIPT_DIR%\templates\mcp.json" "!MCP_JSON!" >nul
        echo   COPY  !MCP_JSON!
    )
    echo.
)

:: ── Skills ────────────────────────────────────────────────────────────────────

if /i "%INSTALL_SKILLS%"=="true" (
    echo Installing Skills...
    set "SRC_SKILLS=%SCRIPT_DIR%\skills"
    set "DST_SKILLS=%TARGET_DIR%\.cursor\skills"
    if not exist "%DST_SKILLS%" mkdir "%DST_SKILLS%"

    for /d %%D in ("%SRC_SKILLS%\*") do (
        call :install_dir_item "%%D" "%DST_SKILLS%\%%~nxD"
    )
    for %%F in ("%SRC_SKILLS%\*") do (
        if exist "%%F" (
            call :install_file "%%F" "%DST_SKILLS%\%%~nxF"
        )
    )
    echo.
)

:: ── Rules ─────────────────────────────────────────────────────────────────────

if /i "%INSTALL_RULES%"=="true" (
    echo Installing Rules...
    set "DST_RULES=%TARGET_DIR%\.cursor\rules"
    if not exist "%DST_RULES%" mkdir "%DST_RULES%"
    for %%F in ("%SCRIPT_DIR%\rules\*.mdc") do (
        if exist "%%F" (
            call :install_file "%%F" "%DST_RULES%\%%~nxF"
        )
    )
    echo.
)

:: ── Agents ────────────────────────────────────────────────────────────────────

if /i "%INSTALL_AGENTS%"=="true" (
    echo Installing Subagents...
    set "DST_AGENTS=%TARGET_DIR%\.cursor\agents"
    if not exist "%DST_AGENTS%" mkdir "%DST_AGENTS%"
    for %%F in ("%SCRIPT_DIR%\agents\*.md") do (
        if exist "%%F" (
            call :install_file "%%F" "%DST_AGENTS%\%%~nxF"
        )
    )
    echo.
)

:: ── Python 依赖 ───────────────────────────────────────────────────────────────

if /i "%INSTALL_DEPS%"=="true" if /i "%INSTALL_MCP%"=="true" (
    echo Installing Python dependencies...
    where pip >nul 2>&1
    if not errorlevel 1 (
        pip install -r "%SCRIPT_DIR%\requirements.txt" -q
        echo   Done.
    ) else (
        where pip3 >nul 2>&1
        if not errorlevel 1 (
            pip3 install -r "%SCRIPT_DIR%\requirements.txt" -q
            echo   Done.
        ) else (
            echo   WARNING: pip not found. Please install dependencies manually:
            echo            pip install -r %SCRIPT_DIR%\requirements.txt
        )
    )
    echo.
)

:: ── 完成 ──────────────────────────────────────────────────────────────────────

echo ========================================
echo   Installation complete!
echo ========================================
echo.
echo Next steps:
echo   1. Open your project in Cursor
echo   2. The MCP Servers will auto-start based on .cursor/mcp.json
echo   3. Skills, Rules, and Subagents are active immediately
echo.
echo To verify MCP tools are available, ask Cursor:
echo   "List all available MCP tools"
echo.

endlocal

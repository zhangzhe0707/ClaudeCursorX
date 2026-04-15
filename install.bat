@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul 2>&1

:: ClaudeCursorX installer for Windows
:: Installs MCP Servers, Skills, Rules, and Subagents into USER-level %USERPROFILE%\.cursor\ directory.
:: This ensures global availability across all Cursor projects.

:: -- Parse arguments --
set "SCRIPT_DIR=%~dp0"
:: Remove trailing backslash
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

:: Default: install to user-level .cursor (global installation)
set "USER_CURSOR_DIR=%USERPROFILE%\.cursor"
set "MODE=copy"
set "INSTALL_MCP=true"
set "INSTALL_SKILLS=true"
set "INSTALL_RULES=true"
set "INSTALL_AGENTS=true"
set "INSTALL_DEPS=true"
set "FORCE_OVERWRITE=false"

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
if /i "%~1"=="--force"        ( set "FORCE_OVERWRITE=true" & shift & goto :parse_args )
if /i "%~1"=="-f"             ( set "FORCE_OVERWRITE=true" & shift & goto :parse_args )
if /i "%~1"=="-h"            ( goto :usage )
if /i "%~1"=="--help"        ( goto :usage )
echo [ERROR] Unknown option: %~1
goto :usage

:usage
echo.
echo Usage: install.bat [OPTIONS]
echo.
echo Install ClaudeCursorX to USER-GLOBAL Cursor directory (%USERPROFILE%\.cursor\)
echo All components are available globally for every Cursor project.
echo.
echo Options:
echo   --link               Use symlinks instead of copying (requires Admin rights)
echo   --copy               Copy files (default, standalone)
echo   --mcp-only           Only install MCP Servers
echo   --skills-only        Only install Skills (Agent Skills)
echo   --rules-only         Only install Rules (.mdc rules)
echo   --agents-only        Only install Subagents
echo   --no-deps            Skip Python dependency installation
echo   --force, -f          Force overwrite existing files
echo   -h, --help           Show this help message
echo.
echo Examples:
echo   install.bat                         (install everything to %USERPROFILE%\.cursor\)
echo   install.bat --mcp-only              (only install MCP Servers)
echo   install.bat --link --force          (use symlinks, overwrite existing)
echo   install.bat --copy --no-deps        (copy files, skip dependency install)
echo.
goto :eof

:args_done

:: -- Verify source directory --

if not exist "%SCRIPT_DIR%\mcp-servers" (
    echo [ERROR] Source directory mcp-servers not found in: %SCRIPT_DIR%
    echo         Make sure you're running install.bat from the ClaudeCursorX root directory.
    exit /b 1
)

:: -- Prepare target directory (user-level) --

set "TARGET_DIR=%USER_CURSOR_DIR%"

:: Ensure parent directory exists
if not exist "%USERPROFILE%" (
    echo [ERROR] User profile directory not found: %USERPROFILE%
    exit /b 1
)

if not exist "%TARGET_DIR%" (
    echo [INFO] Creating target directory: %TARGET_DIR%
    mkdir "%TARGET_DIR%" 2>nul
    if errorlevel 1 (
        echo [ERROR] Failed to create target directory: %TARGET_DIR%
        echo         Check permissions and try again.
        exit /b 1
    )
)

echo ========================================
echo   ClaudeCursorX Installer
echo ========================================
echo   Source:  %SCRIPT_DIR%
echo   Target:  %TARGET_DIR%  (USER-GLOBAL)
echo   Mode:    %MODE%
echo   Force:   %FORCE_OVERWRITE%
echo ========================================
echo.

:: Symlink mode requires admin on Windows
if /i "%MODE%"=="link" (
    echo [INFO] Symlink mode requires Administrator rights or Developer Mode enabled.
    echo        Copy mode will be used automatically if symlink fails.
    echo.
)

:: -- Helper functions --

goto :main

:: install_file src dst
:install_file
    set "_src=%~1"
    set "_dst=%~2"

    :: Check source exists
    if not exist "%_src%" (
        echo   SKIP  %_dst%  (source not found: %_src%)
        goto :eof
    )

    :: Ensure parent directory exists
    for %%D in ("%_dst%") do (
        if not exist "%%~dpD" (
            mkdir "%%~dpD" 2>nul
        )
    )

    :: Skip if exists and not forced
    if exist "%_dst%" (
        if /i "%FORCE_OVERWRITE%"=="false" (
            echo   SKIP  %_dst%  (already exists, use --force to overwrite)
            goto :eof
        )
    )

    if /i "%MODE%"=="link" (
        if exist "%_dst%" (
            del /q "%_dst%" 2>nul || rmdir /s /q "%_dst%" 2>nul
        )
        mklink "%_dst%" "%_src%" >nul 2>&1
        if errorlevel 1 (
            copy /y "%_src%" "%_dst%" >nul
            if errorlevel 1 (
                echo   FAIL  %_dst%  (copy failed)
            ) else (
                echo   COPY  %_dst%  (symlink failed, copied instead)
            )
        ) else (
            echo   LINK  %_dst%  ->  %_src%
        )
    ) else (
        copy /y "%_src%" "%_dst%" >nul
        if errorlevel 1 (
            echo   FAIL  %_dst%  (copy failed)
        ) else (
            echo   COPY  %_dst%
        )
    )
    goto :eof

:: install_dir_item src_dir dst_dir — install a whole directory
:install_dir_item
    set "_src=%~1"
    set "_dst=%~2"

    if not exist "%_src%" (
        echo   SKIP  %_dst%  (source directory not found)
        goto :eof
    )

    :: Skip if exists and not forced
    if exist "%_dst%" (
        if /i "%FORCE_OVERWRITE%"=="false" (
            echo   SKIP  %_dst%  (already exists, use --force to overwrite)
            goto :eof
        )
    )

    if /i "%MODE%"=="link" (
        if exist "%_dst%" ( rmdir /s /q "%_dst%" 2>nul )
        mklink /d "%_dst%" "%_src%" >nul 2>&1
        if errorlevel 1 (
            xcopy /e /i /y /q "%_src%" "%_dst%\" >nul 2>&1
            if errorlevel 1 (
                echo   FAIL  %_dst%  (copy failed)
            ) else (
                echo   COPY  %_dst%  (dir symlink failed, copied instead)
            )
        ) else (
            echo   LINK  %_dst%  ->  %_src%
        )
    ) else (
        if not exist "%_dst%" mkdir "%_dst%" >nul 2>&1
        xcopy /e /i /y /q "%_src%\*" "%_dst%\" >nul 2>&1
        if errorlevel 1 (
            echo   FAIL  %_dst%  (xcopy failed)
        ) else (
            echo   COPY  %_dst%
        )
    )
    goto :eof

:: install_dir_recursive src_dir dst_base — recursive install keeping structure
:install_dir_recursive
    set "_src_dir=%~1"
    set "_dst_base=%~2"

    if not exist "%_src_dir%" (
        goto :eof
    )

    :: Process files in current directory
    for %%F in ("%_src_dir%\*") do (
        if exist "%%F" (
            if not exist "%%F\" (
                set "FILE_NAME=%%~nxF"
                set "DST_FILE=%_dst_base%\!FILE_NAME!"
                call :install_file "%%F" "!DST_FILE!"
            )
        )
    )

    :: Process subdirectories
    for /d %%D in ("%_src_dir%\*") do (
        if exist "%%D" (
            set "DIR_NAME=%%~nxD"
            set "DST_DIR=%_dst_base%\!DIR_NAME!"
            call :install_dir_recursive "%%D" "!DST_DIR!"
        )
    )
    goto :eof

:main

:: -- Install components --

:: MCP Servers
if /i "%INSTALL_MCP%"=="true" (
    echo [1/4] Installing MCP Servers...
    if exist "%SCRIPT_DIR%\mcp-servers\" (
        for /d %%S in ("%SCRIPT_DIR%\mcp-servers\*") do (
            if exist "%%S" (
                set "SERVER_NAME=%%~nxS"
                set "DST_SERVER=%TARGET_DIR%\mcp-servers\!SERVER_NAME!"
                call :install_dir_recursive "%%S" "!DST_SERVER!"
            )
        )
    ) else (
        echo   WARNING: mcp-servers directory not found
    )
    :: Install template mcp.json
    set "MCP_JSON=%TARGET_DIR%\mcp.json"
    set "MCP_JSON_TPL=%SCRIPT_DIR%\templates\mcp.json"
    if exist "%MCP_JSON_TPL%" (
        call :install_file "%MCP_JSON_TPL%" "%MCP_JSON%"
    )
    echo.
)

:: Skills
if /i "%INSTALL_SKILLS%"=="true" (
    echo [2/4] Installing Agent Skills...
    set "SRC_SKILLS=%SCRIPT_DIR%\skills"
    set "DST_SKILLS=%TARGET_DIR%\skills"

    if exist "%SRC_SKILLS%\" (
        for /d %%D in ("%SRC_SKILLS%\*") do (
            if exist "%%D" (
                call :install_dir_item "%%D" "%DST_SKILLS%\%%~nxD"
            )
        )
        :: Install standalone skill files at root
        for %%F in ("%SRC_SKILLS%\*") do (
            if exist "%%F" (
                if not exist "%%F\" (
                    call :install_file "%%F" "%DST_SKILLS%\%%~nxF"
                )
            )
        )
    ) else (
        echo   WARNING: skills directory not found
    )
    echo.
)

:: Rules
if /i "%INSTALL_RULES%"=="true" (
    echo [3/4] Installing Cursor Rules (.mdc)...
    set "DST_RULES=%TARGET_DIR%\rules"

    if exist "%SCRIPT_DIR%\rules\" (
        for %%F in ("%SCRIPT_DIR%\rules\*.mdc") do (
            if exist "%%F" (
                call :install_file "%%F" "%DST_RULES%\%%~nxF"
            )
        )
        :: Check subdirectories
        for /d %%D in ("%SCRIPT_DIR%\rules\*") do (
            if exist "%%D" (
                for %%F in ("%%D\*.mdc") do (
                    if exist "%%F" (
                        call :install_file "%%F" "%DST_RULES%\%%~nxF"
                    )
                )
            )
        )
    ) else (
        echo   WARNING: rules directory not found
    )
    echo.
)

:: Agents
if /i "%INSTALL_AGENTS%"=="true" (
    echo [4/4] Installing Subagents...
    set "DST_AGENTS=%TARGET_DIR%\agents"

    if exist "%SCRIPT_DIR%\agents\" (
        for %%F in ("%SCRIPT_DIR%\agents\*.md") do (
            if exist "%%F" (
                call :install_file "%%F" "%DST_AGENTS%\%%~nxF"
            )
        )
        for %%F in ("%SCRIPT_DIR%\agents\*.mdc") do (
            if exist "%%F" (
                call :install_file "%%F" "%DST_AGENTS%\%%~nxF"
            )
        )
    ) else (
        echo   WARNING: agents directory not found
    )
    echo.
)

:: Python dependencies
if /i "%INSTALL_DEPS%"=="true" if /i "%INSTALL_MCP%"=="true" (
    echo Installing Python dependencies...
    set "REQUIREMENTS=%SCRIPT_DIR%\requirements.txt"
    if exist "%REQUIREMENTS%" (
        where pip >nul 2>&1
        if not errorlevel 1 (
            echo   Running: pip install -r "%REQUIREMENTS%"
            pip install -r "%REQUIREMENTS%"
            if errorlevel 1 (
                echo   WARNING: pip install completed with errors
            ) else (
                echo   Done.
            )
        ) else (
            where pip3 >nul 2>&1
            if not errorlevel 1 (
                echo   Running: pip3 install -r "%REQUIREMENTS%"
                pip3 install -r "%REQUIREMENTS%"
                if errorlevel 1 (
                    echo   WARNING: pip3 install completed with errors
                ) else (
                    echo   Done.
                )
            ) else (
                echo   WARNING: pip/pip3 not found in PATH.
                echo            Please install dependencies manually:
                echo            pip install -r "%REQUIREMENTS%"
            )
        )
    ) else (
        echo   WARNING: requirements.txt not found, skipping.
    )
    echo.
)

:: -- Final summary
echo ========================================
echo   Installation Complete!
echo ========================================
echo.
echo Install location: %TARGET_DIR%
echo.
echo What was installed:
if /i "%INSTALL_MCP%"=="true"    echo   ✅ MCP Servers
if /i "%INSTALL_SKILLS%"=="true"  echo   ✅ Agent Skills
if /i "%INSTALL_RULES%"=="true"   echo   ✅ Cursor Rules
if /i "%INSTALL_AGENTS%"=="true"  echo   ✅ Subagents
echo.
echo Next steps:
echo   1. Restart Cursor to load new MCP Servers
echo   2. All components are now available GLOBALLY across all projects
echo   3. MCP config is at: %USER_CURSOR_DIR%\mcp.json
echo.
echo To verify everything works, ask Cursor in Agent mode:
echo   "List all available MCP tools"
echo.

endlocal

@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Building Soliplex Ingester UI
echo ========================================
echo.
echo This will:
echo   1. Install npm dependencies
echo   2. Build production bundle (with optimized chunking)
echo   3. Apply static filenames and version query strings
echo   4. Copy to server static directory
echo.

REM Navigate to UI directory
cd ui
if errorlevel 1 (
    echo Failed to navigate to ui directory
    exit /b 1
)

REM Install dependencies
echo [1/4] Installing dependencies...
call npm install
if errorlevel 1 (
    echo ERROR: Failed to install dependencies
    exit /b 1
)
echo.

REM Build the UI (includes static-filenames post-processor)
echo [2/4] Building production UI...
echo       - Bundling with Vite (vendor + app chunks)
echo       - Renaming to static filenames
echo       - Adding version query strings
call npm run build
if errorlevel 1 (
    echo ERROR: Failed to build UI
    exit /b 1
)
echo.

REM Copy build artifacts to server static directory
echo [3/4] Copying build artifacts...
if not exist "..\src\soliplex\ingester\server\static" mkdir "..\src\soliplex\ingester\server\static"
xcopy /E /I /Y /Q build\* "..\src\soliplex\ingester\server\static\" >nul
if errorlevel 1 (
    echo ERROR: Failed to copy build artifacts
    exit /b 1
)
echo       Copied to: src\soliplex\ingester\server\static\
echo.

REM Verify key files exist
echo [4/4] Verifying build...
set "STATIC_DIR=..\src\soliplex\ingester\server\static"
set "INDEX_FILE=%STATIC_DIR%\index.html"
set "APP_JS=%STATIC_DIR%\_app\immutable\entry\app.js"
set "START_JS=%STATIC_DIR%\_app\immutable\entry\start.js"

if not exist "%INDEX_FILE%" (
    echo ERROR: index.html not found at %INDEX_FILE%
    exit /b 1
)
echo       [OK] index.html

if not exist "%APP_JS%" (
    echo WARNING: app.js not found at %APP_JS%
) else (
    echo       [OK] app.js (static name)
)

if not exist "%START_JS%" (
    echo WARNING: start.js not found at %START_JS%
) else (
    echo       [OK] start.js (static name)
)
echo.

echo ========================================
echo [SUCCESS] UI build complete!
echo ========================================
echo.
echo Static files ready at: src\soliplex\ingester\server\static\
echo Entry points use static filenames with query string cache busting
echo.

cd ..
endlocal

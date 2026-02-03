@echo off
setlocal enabledelayedexpansion

echo ========================================
echo Building Soliplex Ingester UI
echo ========================================
echo.
echo This will:
echo   1. Install npm dependencies
echo   2. Build production bundle (with optimized chunking)
echo   3. Copy to server static directory
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

REM Build the UI
echo [2/3] Building production UI...
echo       - Bundling with Vite (vendor + app chunks)
echo       - Generating content-hashed filenames for cache busting
call npm run build
if errorlevel 1 (
    echo ERROR: Failed to build UI
    exit /b 1
)
echo.

REM Clear and copy build artifacts to server static directory
echo [3/3] Copying build artifacts...
if exist "..\src\soliplex\ingester\server\static" rmdir /S /Q "..\src\soliplex\ingester\server\static"
mkdir "..\src\soliplex\ingester\server\static"
xcopy /E /I /Y /Q build\* "..\src\soliplex\ingester\server\static\" >nul
if errorlevel 1 (
    echo ERROR: Failed to copy build artifacts
    exit /b 1
)
echo       Copied to: src\soliplex\ingester\server\static\
echo.

echo.

echo ========================================
echo [SUCCESS] UI build complete!
echo ========================================
echo.
echo Static files ready at: src\soliplex\ingester\server\static\
echo Content-hashed filenames provide automatic cache busting
echo.

cd ..
endlocal

@echo off
chcp 65001 >nul
echo ========================================
echo   Ollama 服务启动检查
echo ========================================
echo.

:: 1. 检查 ollama 是否安装
where ollama >nul 2>&1
if %ERRORLEVEL% neq 0 (
    echo [ERROR] 未检测到 Ollama，请先安装：
    echo         https://ollama.com/download
    echo         安装后运行：ollama pull bge-m3
    pause
    exit /b 1
)
echo [OK] Ollama 已安装

:: 2. 检查 Ollama 是否已在运行
curl -s http://localhost:11434/ >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] Ollama 服务已在运行
    echo.
    echo ========================================
    echo   Ollama 就绪，可以启动项目服务
    echo ========================================
    exit /b 0
)

:: 3. 未运行，尝试启动（CPU 模式）
echo [INFO] 正在启动 Ollama (CPU 模式)...
set CUDA_VISIBLE_DEVICES=
start "Ollama-Server" /min ollama serve

:: 4. 等待启动，最多 20 秒
echo [INFO] 等待 Ollama 启动（最多 20 秒）...
set /a retry=0
:wait_loop
timeout /t 2 /nobreak >nul
curl -s http://localhost:11434/ >nul 2>&1
if %ERRORLEVEL% equ 0 (
    echo [OK] Ollama 启动成功
    echo.
    echo ========================================
    echo   Ollama 就绪（纯 CPU 模式），可以启动项目服务
    echo ========================================
    exit /b 0
)
set /a retry+=1
if %retry% lss 10 goto wait_loop

:: 5. 超时
echo.
echo ========================================
echo   [ERROR] Ollama 启动失败或超时
echo.
echo   请尝试手动操作：
echo     1. 打开新终端，运行: set CUDA_VISIBLE_DEVICES=
echo     2. 然后运行: ollama serve
echo     3. 确认 http://localhost:11434/ 可访问后，重新启动本项目
echo ========================================
pause
exit /b 1

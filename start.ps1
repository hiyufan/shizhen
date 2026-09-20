# Windows 一键启动：首次运行会创建 .venv 并安装依赖
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "创建虚拟环境并安装依赖（只需一次）..."
    python -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
    .\.venv\Scripts\python.exe -m pip install -e ".[web,cli]"
}
$port = if ($env:PARSE_VIDEO_PORT) { $env:PARSE_VIDEO_PORT } else { "8000" }
Write-Host "打开 http://127.0.0.1:$port"
.\.venv\Scripts\python.exe main.py

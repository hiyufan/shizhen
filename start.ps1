# Windows 一键启动：需要 Rust 工具链（https://rustup.rs）和 ffmpeg（PATH 上或 data\bin\ffmpeg.exe）
Set-Location $PSScriptRoot
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -and -not (Test-Path data\bin\ffmpeg.exe)) {
    Write-Host "提示：没找到 ffmpeg，转换功能用不了（winget install ffmpeg）"
}
$port = if ($env:PARSE_VIDEO_PORT) { $env:PARSE_VIDEO_PORT } else { 8000 }
Write-Host "打开 http://127.0.0.1:$port"
cargo run --release

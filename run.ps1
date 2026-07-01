# Запуск Сигнал-Скаут локально
# Из папки Сигнал-Скаут: .\run.ps1

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Создан .env — вставьте XMLRIVER_USER и XMLRIVER_KEY (или ключи в брифе) и перезапустите." -ForegroundColor Yellow
}

python -m pip install -r requirements.txt -q
python -m uvicorn server.main:app --host 127.0.0.1 --port 8765 --reload

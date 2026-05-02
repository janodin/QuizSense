# Kill existing servers
Get-NetTCPConnection -LocalPort 8000 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Get-NetTCPConnection -LocalPort 5555 -ErrorAction SilentlyContinue | ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
Start-Sleep 1

# Start Django
Start-Process python -ArgumentList "manage.py runserver 0.0.0.0:8000" -WorkingDirectory "D:\Desktop\Django Projects\QuizSense" -WindowStyle Hidden

# Start Celery
Start-Process python -ArgumentList "-m celery -A quizsense worker --loglevel=info --pool=solo" -WorkingDirectory "D:\Desktop\Django Projects\QuizSense" -WindowStyle Hidden

Start-Sleep 3
Write-Host "Django runserver: http://localhost:8000"
Write-Host "Celery worker: running in background"
Get-NetTCPConnection -LocalPort 8000,5555 -ErrorAction SilentlyContinue | Select-Object LocalPort,State,OwningProcess | Format-Table -AutoSize

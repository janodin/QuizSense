@echo off
cd /d "D:\Desktop\Django Projects\QuizSense"
echo Starting QuizSense Development Server...

echo [1/2] Starting Django development server...
start "QuizSense Server" cmd /k "call venv\Scripts\activate.bat && python manage.py runserver"

timeout /t 3 /nobreak >nul

echo [2/2] Starting Celery worker...
start "QuizSense Celery" cmd /k "call venv\Scripts\activate.bat && celery -A quizsense worker --loglevel=info --pool=threads --concurrency=2 --max-tasks-per-child=10"

echo.
echo Both services are starting in new windows.
echo Django: http://127.0.0.1:8000/
exit

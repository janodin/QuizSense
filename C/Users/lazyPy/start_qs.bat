@echo off
cd /d D:\Desktop\Django Projects\QuizSense
echo Starting Celery worker...
venv\Scripts\python.exe -m celery -A quizsense worker --loglevel=info

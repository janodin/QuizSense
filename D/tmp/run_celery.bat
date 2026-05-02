@echo off
cd /d D:\Desktop\Django Projects\QuizSense
venv\Scripts\python.exe -m celery -A quizsense worker --loglevel=info

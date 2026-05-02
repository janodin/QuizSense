@echo off
cd /d "D:\Desktop\Django Projects\QuizSense"
set USE_POSTGRES=0
venv\Scripts\python.exe manage.py runserver 0.0.0.0:8000

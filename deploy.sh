#!/bin/bash
# QuizSense Deployment Script for Hetzner CX22
# Run this on your Hetzner VPS after pushing to GitHub

set -e  # Exit on any error

echo "=========================================="
echo "QuizSense Deployment Script"
echo "=========================================="

# Configuration (update these if your paths differ)
PROJECT_DIR="/opt/quizsense"
VENV_DIR="$PROJECT_DIR/venv"
USER="root"  # Change to your deploy user if not root

echo "[1/8] Navigating to project directory..."
cd "$PROJECT_DIR"

echo "[2/8] Pulling latest code from GitHub..."
git pull origin main

echo "[3/8] Activating virtual environment..."
source "$VENV_DIR/bin/activate"

echo "[4/8] Installing/updating dependencies..."
pip install --upgrade pip
pip install -r requirements.txt

echo "[5/8] Running Django migrations..."
python manage.py migrate --noinput

echo "[6/8] Collecting static files..."
python manage.py collectstatic --noinput

echo "[7/8] Restarting services..."
systemctl restart gunicorn
systemctl restart celery
systemctl restart redis

echo "[8/8] Running health checks..."
sleep 3

echo ""
echo "=========================================="
echo "Health Check Results"
echo "=========================================="

echo ""
echo "--- Gunicorn Status ---"
if systemctl is-active --quiet gunicorn; then
    echo "Gunicorn: RUNNING"
    echo "Workers: $(ps aux | grep 'gunicorn: worker' | grep -v grep | wc -l)"
else
    echo "Gunicorn: FAILED"
    systemctl status gunicorn --no-pager
fi

echo ""
echo "--- Celery Status ---"
if systemctl is-active --quiet celery; then
    echo "Celery: RUNNING"
else
    echo "Celery: FAILED"
    systemctl status celery --no-pager
fi

echo ""
echo "--- Redis Status ---"
if systemctl is-active --quiet redis; then
    echo "Redis: RUNNING"
    redis-cli ping
else
    echo "Redis: FAILED"
fi

echo ""
echo "--- Memory Usage ---"
free -h | grep "Mem:"

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Visit your site and upload a test PDF"
echo "2. Watch logs: sudo journalctl -u celery -f"
echo "3. Check memory: sudo journalctl -u gunicorn -f | grep MEMORY"


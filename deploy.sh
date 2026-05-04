#!/bin/bash
# QuizSense Deployment Script for Hetzner CX22
# Run this on your Hetzner VPS after pushing to GitHub
#
# Usage:
#   ssh root@YOUR_HETZNER_IP "bash -s" < deploy.sh
#   OR
#   bash deploy.sh

set -euo pipefail  # Strict mode: exit on error, undefined var, pipe fail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Configuration (update these if your paths differ)
PROJECT_DIR="${PROJECT_DIR:-/opt/quizsense}"
VENV_DIR="${VENV_DIR:-$PROJECT_DIR/venv}"
USER="${DEPLOY_USER:-root}"

echo "=========================================="
echo "QuizSense Deployment Script"
echo "=========================================="
echo ""

# 0. Verify we're on the server
echo "[0/9] Environment checks..."
if [ ! -d "$PROJECT_DIR" ]; then
    log_error "Project directory $PROJECT_DIR does not exist."
    log_info "If this is a fresh server, run the first-time setup first."
    exit 1
fi

cd "$PROJECT_DIR"

# 1. Load environment variables from .env
if [ -f "$PROJECT_DIR/.env" ]; then
    log_info "Loading environment from .env..."
    set -a  # Automatically export all variables
    source "$PROJECT_DIR/.env"
    set +a
else
    log_warn ".env file not found at $PROJECT_DIR/.env"
    log_warn "Some features may not work without environment variables."
fi

# Verify critical env vars are set
MISSING_VARS=()
[ -z "${SECRET_KEY:-}" ] && MISSING_VARS+=("SECRET_KEY")
[ -z "${DATABASE_URL:-}" ] && [ -z "${POSTGRES_DB:-}" ] && MISSING_VARS+=("DATABASE_URL or POSTGRES_DB")
[ -z "${REDIS_URL:-}" ] && MISSING_VARS+=("REDIS_URL")

if [ ${#MISSING_VARS[@]} -ne 0 ]; then
    log_warn "Missing environment variables: ${MISSING_VARS[*]}"
    log_warn "Some features may not work correctly."
fi

echo ""
echo "[1/9] Pulling latest code from GitHub..."
git fetch origin
git reset --hard origin/main
GIT_COMMIT=$(git rev-parse --short HEAD)
log_info "Deployed commit: $GIT_COMMIT"

echo ""
echo "[2/9] Activating virtual environment..."
if [ ! -f "$VENV_DIR/bin/activate" ]; then
    log_error "Virtual environment not found at $VENV_DIR"
    log_info "Run: python3 -m venv $VENV_DIR"
    exit 1
fi
source "$VENV_DIR/bin/activate"

echo ""
echo "[3/9] Installing/updating dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
log_info "Dependencies installed."

echo ""
echo "[4/9] Running Django system checks..."
python manage.py check --deploy || {
    log_warn "Django deployment check found issues (non-critical)"
}

echo ""
echo "[5/9] Running database migrations..."
python manage.py migrate --noinput

echo ""
echo "[6/9] Collecting static files..."
python manage.py collectstatic --noinput --clear

echo ""
echo "[7/9] Restarting services..."
systemctl daemon-reload
systemctl restart gunicorn || {
    log_warn "Failed to restart gunicorn. Is the service installed?"
    log_info "Run: cp systemd/gunicorn.service /etc/systemd/system/ && systemctl daemon-reload"
}
systemctl restart celery || {
    log_warn "Failed to restart celery. Is the service installed?"
    log_info "Run: cp systemd/celery.service /etc/systemd/system/ && systemctl daemon-reload"
}
systemctl restart redis || log_warn "Redis restart failed (may be managed differently)"

# Wait for services to start
sleep 3

echo ""
echo "=========================================="
echo "Health Checks"
echo "=========================================="

# Gunicorn
echo ""
echo "--- Gunicorn ---"
if systemctl is-active --quiet gunicorn; then
    log_info "Gunicorn: RUNNING"
    WORKERS=$(ps aux | grep 'gunicorn: worker' | grep -v grep | wc -l)
    log_info "Worker processes: $WORKERS"
else
    log_error "Gunicorn: FAILED"
    systemctl status gunicorn --no-pager || true
fi

# Celery
echo ""
echo "--- Celery Worker ---"
if systemctl is-active --quiet celery; then
    log_info "Celery: RUNNING"
    # Show Celery config
    CELERY_LIMIT=$(systemctl show celery --property=Environment | grep -o 'CELERY_WORKER_MAX_MEMORY_PER_CHILD=[^ ]*' || echo "N/A")
    log_info "Memory limit: $CELERY_LIMIT"
else
    log_error "Celery: FAILED"
    systemctl status celery --no-pager || true
fi

# Redis
echo ""
echo "--- Redis ---"
if redis-cli ping &>/dev/null; then
    log_info "Redis: RUNNING (PONG)"
else
    log_warn "Redis: Not responding to ping"
fi

# Django
echo ""
echo "--- Django Health ---"
if curl -sf http://localhost:8000/health/ &>/dev/null || curl -sf http://127.0.0.1:8000/ &>/dev/null; then
    log_info "Django: RESPONDING"
else
    log_warn "Django: Not responding on localhost:8000 (may need nginx)"
fi

# Memory
echo ""
echo "--- Server Memory ---"
free -h | grep "Mem:" || true

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "Commit: $GIT_COMMIT"
echo "=========================================="
echo ""
echo "Next steps:"
echo "1. Visit your site and upload a test PDF"
echo "2. Watch Celery logs:  sudo journalctl -u celery -f"
echo "3. Watch Gunicorn:     sudo journalctl -u gunicorn -f"
echo "4. Check memory usage: sudo journalctl -u celery | grep MEMORY"
echo ""

# Hetzner Cloud CLI integration (optional)
if command -v hcloud &> /dev/null; then
    echo "Hetzner Cloud CLI detected."
    if [ -n "${HETZNER_API_KEY:-}" ]; then
        export HCLOUD_TOKEN="$HETZNER_API_KEY"
        log_info "Hetzner API key loaded from .env"
    fi
fi

exit 0


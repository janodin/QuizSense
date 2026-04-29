import os
import time

import paramiko


def _run(client, command: str) -> tuple[str, str, int]:
    """Execute command over SSH, return (stdout, stderr, exit_status)."""
    stdin, stdout, stderr = client.exec_command(command)
    exit_status = stdout.channel.recv_exit_status()
    stdout_text = stdout.read().decode("utf-8", errors="replace")
    stderr_text = stderr.read().decode("utf-8", errors="replace")
    return stdout_text, stderr_text, exit_status


def _systemd_service() -> str:
    return """[Unit]
Description=QuizSense Gunicorn (RAM-optimised: 2 workers, preload)
After=network.target postgresql.service

[Service]
User=root
WorkingDirectory=/opt/quizsense
EnvironmentFile=/opt/quizsense/.env
# Gunicorn args tuned for Hetzner CX22 (4 GB RAM):
#   workers=2      : 1 = SPOF, 3+ = OOM on 4 GB
#   threads=1      : single-threaded; background work in daemon threads
#   timeout=300    : PDF OCR + embedding + MiniMax API can take minutes
#   preload-app    : COW fork sharing reduces RSS at startup
ExecStart=/opt/quizsense/.venv/bin/gunicorn \\
    quizsense.wsgi:application \\
    --bind 0.0.0.0:8000 \\
    --workers 2 \\
    --threads 1 \\
    --timeout 300 \\
    --worker-connections 1000 \\
    --preload-app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
"""


def deploy():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    key = paramiko.Ed25519Key.from_private_key_file(os.path.expanduser("~/.ssh/id_ed25519_quizsense"))

    max_retries = 15
    for i in range(max_retries):
        try:
            print(f"Connecting to production server... Attempt {i + 1}")
            client.connect("178.104.226.86", username="root", pkey=key, timeout=10)
            break
        except Exception as e:
            print(f"Waiting for server reboot... {e}")
            time.sleep(10)
    else:
        print("Failed to connect after retries.")
        return

    print("Connected!")

    # ── Pre-flight checks ────────────────────────────────────────────────────
    print("\n=== Pre-flight checks ===")

    # Swap check — OOM killer is much more likely without swap
    stdout, stderr, status = _run(client, "free -m | awk '/Swap:/ {print $2}'")
    swap_mb = int(stdout.strip()) if stdout.strip().isdigit() else 0
    if swap_mb == 0:
        print("WARNING: No swap configured! Adding 2 GB swap file...")
        swap_cmd = """
        fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
        chmod 600 /swapfile
        mkswap /swapfile
        swapon /swapfile
        grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
        free -m | grep Swap
        """
        sout, serr, scode = _run(client, swap_cmd)
        print("Swap STDOUT:", sout)
        if scode != 0:
            print("Swap setup FAILED (may need sudo):", serr)
        else:
            print("Swap configured:", sout.strip())
    else:
        print(f"Swap: {swap_mb} MB — OK")

    # ── PostgreSQL memory tuning ─────────────────────────────────────────────
    print("\n=== PostgreSQL RAM tuning ===")
    pg_tune = (
        "sed -i '/^shared_buffers/d' /etc/postgresql/*/main/postgresql.conf; "
        "sed -i '/^effective_cache_size/d' /etc/postgresql/*/main/postgresql.conf; "
        "sed -i '/^work_mem/d' /etc/postgresql/*/main/postgresql.conf; "
        "sed -i '/^maintenance_work_mem/d' /etc/postgresql/*/main/postgresql.conf; "
        "echo \"shared_buffers = 256MB\" >> /etc/postgresql/*/main/postgresql.conf; "
        "echo \"effective_cache_size = 512MB\" >> /etc/postgresql/*/main/postgresql.conf; "
        "echo \"work_mem = 16MB\" >> /etc/postgresql/*/main/postgresql.conf; "
        "echo \"maintenance_work_mem = 128MB\" >> /etc/postgresql/*/main/postgresql.conf; "
        "echo \"max_connections = 20\" >> /etc/postgresql/*/main/postgresql.conf; "
        "systemctl restart postgresql; "
        "echo 'PG tuned OK'"
    )
    sout, serr, scode = _run(client, pg_tune)
    print(sout.strip() or "PG tuning:", serr.strip() if scode != 0 else "OK")

    # ── App deployment ───────────────────────────────────────────────────────
    print("\n=== App deployment ===")
    deploy_cmds = f"""
    cd /opt/quizsense
    git fetch origin
    git reset --hard origin/main
    source .venv/bin/activate
    pip install -r requirements.txt
    python manage.py collectstatic --noinput
    python manage.py migrate

    # Write optimised systemd unit (always replace — ensures latest config)
    cat > /etc/systemd/system/quizsense.service << 'EOFSERVICE'
{_systemd_service()}
EOFSERVICE

    systemctl daemon-reload
    systemctl restart quizsense
    systemctl status quizsense --no-pager
    """

    stdout, stderr, status = _run(client, deploy_cmds)
    stdout_clean = stdout.encode("ascii", errors="replace").decode("ascii")
    stderr_clean = stderr.encode("ascii", errors="replace").decode("ascii")
    print("STDOUT:", stdout_clean[-3000:] if len(stdout_clean) > 3000 else stdout_clean)
    if stderr_clean.strip():
        print("STDERR:", stderr_clean[-1000:])
    print("Exit status:", status)

    if status != 0:
        print("Deploy failed!")
        client.close()
        return

    # ── Post-deploy health ────────────────────────────────────────────────────
    print("\n=== Post-deploy health ===")
    mem_out, mem_err, mem_code = _run(
        client,
        "free -m | awk '/Mem:/ {print \"RAM: \"$3\" MB used / \"$2\" MB total\"} /Swap:/ {print \"Swap: \"$3\" MB used / \"$2\" MB total\"}'",
    )
    print(mem_out.strip())

    print("\nDeploy complete!")
    client.close()


if __name__ == "__main__":
    deploy()


import paramiko
import time
import os

def deploy():
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    
    key = paramiko.RSAKey.from_private_key_file(os.path.expanduser("~/.ssh/id_rsa_quizsense"))
    
    max_retries = 15
    for i in range(max_retries):
        try:
            print(f"Connecting to production server... Attempt {i+1}")
            client.connect('178.104.226.86', username='root', pkey=key, timeout=10)
            break
        except Exception as e:
            print(f"Wait for server reboot... {e}")
            time.sleep(10)
    else:
        print("Failed to connect after retries.")
        return

    print("Connected! Deploying to /opt/quizsense...")
    commands = """
    cd /opt/quizsense
    git fetch origin
    git reset --hard origin/main
    # Ensure dependencies are updated
    source .venv/bin/activate
    pip install -r requirements.txt
    python manage.py collectstatic --noinput
    python manage.py migrate
    # Update Gunicorn timeout in the systemd service if it exists
    sed -i 's/--timeout [0-9]*/--timeout 600/g' /etc/systemd/system/quizsense.service 2>/dev/null || true
    systemctl daemon-reload
    systemctl restart quizsense
    systemctl status quizsense --no-pager
    """
    stdin, stdout, stderr = client.exec_command(commands)

    exit_status = stdout.channel.recv_exit_status()
    stdout_text = stdout.read().decode('utf-8', errors='replace')
    stderr_text = stderr.read().decode('utf-8', errors='replace')
    # Replace non-ascii chars for Windows console compatibility
    stdout_clean = stdout_text.encode('ascii', errors='replace').decode('ascii')
    stderr_clean = stderr_text.encode('ascii', errors='replace').decode('ascii')
    print("STDOUT:", stdout_clean)
    print("STDERR:", stderr_clean)
    print("Exit status:", exit_status)

    client.close()

if __name__ == "__main__":
    deploy()

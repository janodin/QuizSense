@echo off
ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 root@178.104.226.86 "cd /opt/quizsense && git log --oneline -10 && echo --- && cat .env | grep -v SECRET | head -10"

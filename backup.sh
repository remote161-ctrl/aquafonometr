#!/bin/bash
BACKUP_DIR="/home/scr/SPEEDTEST/backups"
mkdir -p "$BACKUP_DIR"
DATE=$(date +%Y%m%d_%H%M%S)
cp /home/scr/SPEEDTEST/data/speedtest.db "$BACKUP_DIR/speedtest_$DATE.db"
find "$BACKUP_DIR" -name "speedtest_*.db" -mtime +14 -delete 2>/dev/null
echo "[$(date)] Backup done: speedtest_$DATE.db"

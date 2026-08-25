#!/usr/bin/env python3
import subprocess, sys, os
os.chdir("/home/scr/SPEEDTEST")
subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "main:app",
     "--host", "0.0.0.0", "--port", "9090",
     "--workers", "4",
     "--ssl-certfile", "cert.pem", "--ssl-keyfile", "key.pem"],
    stdout=open("server.log", "w"),
    stderr=subprocess.STDOUT,
    start_new_session=True
)

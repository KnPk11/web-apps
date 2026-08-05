#!/usr/bin/env python3
import subprocess
import time
import os
import signal
import sys

# Paths
BASE_DIR = "/home/k/web-apps/story-cards"
APP_CMD = ["python3", "-u", "app.py"]
WORKER_CMD = ["python3", "-u", "review_worker.py"]

def start_process(cmd, log_file):
    with open(log_file, "a") as f:
        return subprocess.Popen(cmd, stdout=f, stderr=f, cwd=BASE_DIR, preexec_fn=os.setpgrp)

def main():
    print(f"Starting StoryCards Manager at {time.ctime()}")
    
    # Kill existing to avoid port conflicts
    subprocess.run(["pkill", "-f", "app.py"])
    subprocess.run(["pkill", "-f", "review_worker.py"])
    time.sleep(1)

    app_proc = start_process(APP_CMD, "app.log")
    worker_proc = start_process(WORKER_CMD, "worker.log")

    while True:
        time.sleep(5)
        
        # Check App
        if app_proc.poll() is not None:
            print("App died. Restarting...")
            app_proc = start_process(APP_CMD, "app.log")
            
        # Check Worker
        if worker_proc.poll() is not None:
            print("Worker died. Restarting...")
            worker_proc = start_process(WORKER_CMD, "worker.log")

if __name__ == "__main__":
    # Move to background and detach
    if os.fork() > 0:
        sys.exit(0)
    
    os.setsid()
    if os.fork() > 0:
        sys.exit(0)
        
    main()

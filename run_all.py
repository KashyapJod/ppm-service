import subprocess
import sys
import os
import time
import signal

def run_all():
    print("=" * 60)
    print(" YK-8000C PATIENT MONITOR INTEGRATION ORCHESTRATOR")
    print("=" * 60)
    
    # Path to virtual environment python
    if os.name == 'nt':
        python_exe = os.path.join(".venv", "Scripts", "python.exe")
    else:
        python_exe = os.path.join(".venv", "bin", "python")
        
    if not os.path.exists(python_exe):
        print(f"Error: Virtual environment python not found at {python_exe}")
        print("Please ensure you initialized the virtual environment in '.venv'.")
        sys.exit(1)

    # 1. Run database setup first
    print("\n[Orchestrator] Running Database Setup...")
    try:
        subprocess.run([python_exe, "db_setup.py"], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Database setup failed: {e}")
        sys.exit(1)
        
    processes = []
    
    # Helper to start process
    def start_proc(name, args):
        print(f"[Orchestrator] Starting {name}...")
        # On Windows, we can use creationflags to start in a new console or hide, 
        # but capturing stdout into stdout is fine. Let's redirect stdout to none or print prefixed.
        proc = subprocess.Popen([python_exe] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        processes.append((name, proc))
        
        # Spawn a thread to read and print stdout of this process with a prefix
        import threading
        def log_reader():
            for line in iter(proc.stdout.readline, ''):
                print(f"[{name}] {line.strip()}")
            proc.stdout.close()
            
        t = threading.Thread(target=log_reader, daemon=True)
        t.start()
        return proc

    try:
        # Start core infrastructure
        start_proc("Pi-Bridge", ["pi_bridge.py"])
        start_proc("CMS-Sim", ["cms_sim.py"])
        start_proc("Sniffer", ["packet_capture.py"])
        start_proc("Ingestion", ["ingestion.py"])
        
        # Start API backend
        start_proc("API-Backend", ["-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", "8000", "--log-level", "warning"])
        
        # Give services a couple seconds to bind sockets
        print("\n[Orchestrator] Waiting for sockets to bind...")
        time.sleep(3)
        
        # Start simulators
        start_proc("PPM-Sims", ["ppm_sim.py"])
        
        print("\n" + "=" * 60)
        print(" SYSTEM IS LIVE!")
        print(" -> Open Dashboard: http://127.0.0.1:8000/static/index.html")
        print(" -> Press Ctrl+C to terminate all simulators and servers.")
        print("=" * 60 + "\n")
        
        while True:
            # Check if any process terminated unexpectedly
            for name, proc in processes:
                code = proc.poll()
                if code is not None:
                    print(f"\n[Orchestrator] Process {name} exited with code {code}.")
                    raise KeyboardInterrupt
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\n[Orchestrator] Shutting down all processes...")
        for name, proc in processes:
            print(f"[Orchestrator] Terminating {name}...")
            # On Windows, taskkill or standard terminate
            proc.terminate()
            
        # Wait for termination
        for name, proc in processes:
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
        print("[Orchestrator] All processes successfully terminated.")

if __name__ == "__main__":
    run_all()

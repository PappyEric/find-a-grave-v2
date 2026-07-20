import os
import sys
import subprocess
import time
import webbrowser
import socket

def is_port_in_use(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(('127.0.0.1', port)) == 0

def main():
    print("="*60)
    print("           Find a Grave Tools Local GUI Launcher")
    print("="*60)
    
    # 1. Path to virtualenv Python interpreter
    venv_python = os.path.join('.venv', 'Scripts', 'python.exe')
    if not os.path.exists(venv_python):
        print("Error: Virtual environment (.venv) not found.")
        print("Please create the virtual environment first using:")
        print("  python -m venv .venv")
        print("  .venv\\Scripts\\pip install requests beautifulsoup4 xlsxwriter flask")
        sys.exit(1)
        
    print("[1/3] Virtual environment located.")
    
    # 2. Check if server already running
    port = 5050
    if is_port_in_use(port):
        print(f"Port {port} is already in use. Opening browser directly...")
        webbrowser.open(f"http://127.0.0.1:{port}")
        return
        
    # 3. Start Flask app as background subprocess
    print("[2/3] Starting Local Flask Web Server...")
    server_process = subprocess.Popen([venv_python, 'app.py'])
    
    # Wait for server to start
    print("[3/3] Waiting for server to spin up...")
    retries = 15
    started = False
    while retries > 0:
        if is_port_in_use(port):
            started = True
            break
        time.sleep(0.5)
        retries -= 1
        
    if started:
        print(f"\nServer successfully started at http://127.0.0.1:{port}")
        print("Opening dashboard in your web browser...")
        webbrowser.open(f"http://127.0.0.1:{port}")
    else:
        print("\nWarning: Port check timed out. Attempting to open browser anyway...")
        webbrowser.open(f"http://127.0.0.1:{port}")
        
    print("\nPress Ctrl+C in this terminal window to stop the server and exit.")
    print("="*60)
    
    try:
        # Keep launcher alive until process ends or Ctrl+C
        server_process.wait()
    except KeyboardInterrupt:
        print("\nShutdown signal received. Stopping Flask server...")
        server_process.terminate()
        server_process.wait()
        print("Server stopped. Goodbye!")

if __name__ == '__main__':
    main()

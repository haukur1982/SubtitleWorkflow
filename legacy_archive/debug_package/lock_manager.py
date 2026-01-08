import os
import sys
import fcntl
import atexit
import signal
import psutil
from pathlib import Path

class ProcessLock:
    """
    Ensures only one instance of a script runs at a time using OS-level file locking (fcntl).
    Includes PID verification to detect stale locks or zombie processes.
    """
    def __init__(self, name):
        self.name = name
        self.lock_file = Path(f"/tmp/{name}.lock")
        self.fp = None

    def __enter__(self):
        try:
            # Open in append+read mode to avoid truncating existing PID if locked
            self.fp = open(self.lock_file, 'a+')
            self.fp.seek(0)
            
            # Try to acquire an exclusive lock
            fcntl.lockf(self.fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
            
            # --- LOCK ACQUIRED ---
            # Now it's safe to truncate and write our PID
            self.fp.seek(0)
            self.fp.truncate()
            self.fp.write(str(os.getpid()))
            self.fp.flush()
            
            # Register cleanup
            atexit.register(self.cleanup)
            signal.signal(signal.SIGTERM, self.handle_signal)
            signal.signal(signal.SIGINT, self.handle_signal)
            
            print(f"🔒 Acquired lock for {self.name} (PID: {os.getpid()})")
            return self
            
        except (IOError, BlockingIOError):
            # Lock is held by another process
            self.check_existing_lock()
            sys.exit(1)
            
        except PermissionError:
             print(f"❌ ERROR: Permission denied for lock file '{self.lock_file}'.")
             sys.exit(1)

    def check_existing_lock(self):
        """Reads the PID from the lock file and checks if it's alive."""
        try:
            self.fp.seek(0)
            content = self.fp.read().strip()
            if content:
                pid = int(content)
                if psutil.pid_exists(pid):
                    print(f"❌ ERROR: {self.name} is already running (PID: {pid}).")
                    return
                else:
                    print(f"⚠️ Lock held by dead PID {pid}? (Zombie/Stale)")
                    # In theory, fcntl should release if dead. 
                    # If we are here, the OS thinks it's alive or another process holds it.
            else:
                print(f"❌ ERROR: {self.name} is locked by an unknown process (Empty lock file).")
        except Exception as e:
             print(f"❌ ERROR: {self.name} is locked. Unable to read PID: {e}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.cleanup()

    def handle_signal(self, signum, frame):
        print(f"\n🛑 Received signal {signum}. Cleaning up...")
        self.cleanup()
        sys.exit(0)

    def cleanup(self):
        if self.fp:
            try:
                # Unlock and close
                fcntl.lockf(self.fp, fcntl.LOCK_UN)
                self.fp.close()
                # Only remove if we created it/held it
                if self.lock_file.exists():
                    os.remove(self.lock_file)
            except Exception:
                pass
            self.fp = None

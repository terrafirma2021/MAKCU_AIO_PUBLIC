import sys
import os
import ctypes
import signal

# -------------------------
# Admin Check & Elevation
# -------------------------
def is_admin():
    """Return True if the script is running with administrative privileges."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False

def run_as_admin():
    """Relaunch the script with administrative privileges and exit current instance."""
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable, " ".join(sys.argv), None, 1
        )
        os.kill(os.getpid(), signal.SIGTERM)  # Immediately stop current process
    except:
        sys.exit(1)

# -------------------------
# Relaunch with Admin If Needed
# -------------------------
if __name__ == "__main__":
    if not is_admin():
        run_as_admin()  # Relaunches with elevated rights

    # -------------------------
    # Prepare Import Paths
    # -------------------------
    base_dir = os.path.dirname(os.path.abspath(__file__))
    modules_dir = os.path.join(base_dir, 'modules')
    if modules_dir not in sys.path:
        sys.path.insert(0, modules_dir)

    # -------------------------
    # Load GUI After Elevation
    # -------------------------
    import customtkinter as ctk
    from modules.gui import GUI

    root = ctk.CTk()
    app = GUI(root, is_admin)  # Pass in the is_admin function for internal use
    root.mainloop()


# pyinstaller --onefile --noconsole --uac-admin --name MAKCU --add-data "assets;assets" --add-data "modules;modules" --add-data "assets/app.manifest;." --add-data "assets/driver;assets/driver" main.py



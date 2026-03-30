import importlib
import sys
import os
import time
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class ModuleReloader(FileSystemEventHandler):
    """
    Watches for file changes in the python path and reloads modules dynamically.
    This allows the agent to update its own code and have those changes take effect immediately
    without restarting the process.
    """
    def __init__(self, watch_dir: str):
        self.watch_dir = watch_dir
        self.last_reload = 0
        self.reload_cooldown = 1.0 # Seconds

    def on_modified(self, event):
        if event.is_directory:
            return
        
        if not event.src_path.endswith(".py"):
            return

        # Debounce
        current_time = time.time()
        if current_time - self.last_reload < self.reload_cooldown:
            return
        self.last_reload = current_time

        print(f"\n[HotReload] Detected change in: {event.src_path}")
        self.reload_module(event.src_path)

    def reload_module(self, file_path: str):
        try:
            # Convert file path to module name
            rel_path = os.path.relpath(file_path, self.watch_dir)
            module_name = rel_path.replace(os.sep, ".").replace(".py", "")
            
            # Handle __init__.py
            if module_name.endswith(".__init__"):
                module_name = module_name[:-9]
                
            # Prepend package name 'logicore' since we are watching the logicore dir
            # This is a bit hardcoded but necessary for this project structure
            full_module_name = f"logicore.{module_name}"
            
            # Check if module is loaded
            if full_module_name in sys.modules:
                print(f"[HotReload] Reloading module: {full_module_name}")
                importlib.reload(sys.modules[full_module_name])
                print(f"[HotReload] Successfully reloaded {full_module_name}")
            elif module_name in sys.modules: # Fallback
                print(f"[HotReload] Reloading module: {module_name}")
                importlib.reload(sys.modules[module_name])
                print(f"[HotReload] Successfully reloaded {module_name}")
            else:
                # If it's a new module or not imported yet, try importing it
                # This might be tricky if it's not in sys.modules
                pass 
                
        except Exception as e:
            print(f"[HotReload] Failed to reload {file_path}: {e}")

def start_reloader(watch_dir: str):
    """Starts the background file watcher."""
    event_handler = ModuleReloader(watch_dir)
    observer = Observer()
    observer.schedule(event_handler, watch_dir, recursive=True)
    observer.start()
    return observer

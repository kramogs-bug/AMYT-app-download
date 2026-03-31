"""
storage/logger.py
Handles all logging for the macro app.
Logs go to both the console (optional), a rotating file, and are accessible from the UI.

Fixes applied:
- [BUG FIX] Removed broken manual file-handle management that leaked handles on every log() call.
  The old code tried to reopen self._file_handler.stream every call and emit() with an
  incomplete LogRecord (only 'msg' was set — no level, no lineno, etc.), causing
  incorrect log output and resource leaks.
- [BUG FIX] RotatingFileHandler is now used correctly via the standard logging module.
- [IMPROVEMENT] Console output is opt-in via constructor flag (default ON for dev).
  Disable it in production tight loops to avoid stdout I/O blocking the GIL.
"""

import sys
import os
import datetime
import logging
from logging.handlers import RotatingFileHandler
import time

class Logger:
    def __init__(self, console_output: bool = True, cooldown: float = 10.0):
        self.logs = []
        self._max_memory_logs = 500
        # In a frozen .exe built with console=False there is no console window —
        # printing is silently dropped at best, and crashes on non-cp1252 characters
        # (e.g. → U+2192) at worst.  Auto-disable console output when frozen.
        import sys as _sys
        self._console = console_output and not getattr(_sys, 'frozen', False)
        self._log_cooldown = cooldown
        self._last_log_time = {}

        os.makedirs("logs", exist_ok=True)
        self.log_file = os.path.join("logs", "macro.log")

        # FIX: Use the standard logging module correctly instead of manually
        # reopening and writing to the handler's stream on every call.
        self._logger = logging.getLogger("macro_app")
        self._logger.setLevel(logging.DEBUG)

        # Avoid adding duplicate handlers if Logger is instantiated more than once
        if not self._logger.handlers:
            handler = RotatingFileHandler(
                self.log_file,
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)

        # Prevent log records from bubbling up to the root logger (avoids duplicate output)
        self._logger.propagate = False

    def log(self, message: str, level: str = "INFO"):
        """
        Log a message with a timestamp and level.
        Duplicate messages within the cooldown period are suppressed.
        """
        # Cooldown check
        now = time.time()
        key = (message, level.upper())
        last = self._last_log_time.get(key, 0)
        if now - last < self._log_cooldown:
            # Suppress this log entirely (no file, no console, no memory)
            return
        self._last_log_time[key] = now

        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        entry = f"{timestamp} [{level}] {message}"

        # In‑memory log (capped)
        self.logs.append(entry)
        if len(self.logs) > self._max_memory_logs:
            self.logs = self.logs[-self._max_memory_logs:]

        # Write to rotating file (via standard logging)
        try:
            log_level = {
                "INFO":  logging.INFO,
                "WARN":  logging.WARNING,
                "ERROR": logging.ERROR,
            }.get(level.upper(), logging.INFO)
            self._logger.log(log_level, entry)
        except Exception:
            # Fallback direct append
            try:
                with open(self.log_file, "a", encoding="utf-8") as f:
                    f.write(entry + "\n")
            except Exception:
                pass

        # Optional console output
        if self._console:
            try:
                print(entry)
            except (UnicodeEncodeError, UnicodeDecodeError):
                # Fallback: re-encode with replacement characters so a single
                # unencodable char (e.g. → U+2192 on cp1252 stdout) never crashes
                safe = entry.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
                    sys.stdout.encoding or "utf-8", errors="replace"
                )
                print(safe)

    def get_logs(self):
        """Return all in-memory logs as a list (used by the UI)."""
        return self.logs

    def clear(self):
        """Clear all logs from memory and truncate the log file."""
        self.logs = []
        # BUG FIX: The old code used open(file,'w').close() without a context manager.
        # If .close() raised an exception the file handle would leak.
        # More importantly, the RotatingFileHandler still holds an open handle to the
        # same file — truncating it externally causes a handle conflict on Windows.
        # Correct approach: close the handler, truncate the file, then re-add the handler.
        try:
            for handler in self._logger.handlers[:]:
                handler.close()
                self._logger.removeHandler(handler)
            with open(self.log_file, "w", encoding="utf-8"):
                pass  # truncate
            handler = RotatingFileHandler(
                self.log_file,
                maxBytes=1_000_000,
                backupCount=3,
                encoding="utf-8"
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
            self._logger.addHandler(handler)
        except Exception:
            pass
        if self._console:
            print("[Logger] Logs cleared.")
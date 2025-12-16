import atexit
import logging
from logging.handlers import RotatingFileHandler, QueueHandler, QueueListener
from queue import Queue
from pathlib import Path
from config.settings import LOG_LEVEL

# One queue + one listener PER log_file, so scanner.log and debug_scanner.log
# don't step on each other and each rotates safely.
_LISTENERS_BY_FILE: dict[str, QueueListener] = {}
_QUEUES_BY_FILE: dict[str, Queue] = {}

def _ensure_listener(log_file: str, level, max_bytes: int, backup_count: int) -> QueueHandler:
    log_file = str(Path(log_file))
    if log_file in _LISTENERS_BY_FILE:
        return QueueHandler(_QUEUES_BY_FILE[log_file])

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)

    q: Queue = Queue(-1)
    _QUEUES_BY_FILE[log_file] = q

    file_handler = RotatingFileHandler(
        log_file,
        mode="a",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=True,  # ok here; listener owns the file handle lifetime
    )
    # Helps on Windows even in single-writer mode
    file_handler.closeOnRollover = True

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    file_handler.setFormatter(formatter)

    listener = QueueListener(q, file_handler, respect_handler_level=True)
    listener.start()
    _LISTENERS_BY_FILE[log_file] = listener

    # Ensure clean shutdown so buffers flush and file handles close
    def _stop_listener():
        try:
            listener.stop()
        except Exception:
            pass

    atexit.register(_stop_listener)

    return QueueHandler(q)

def setup_logger(
    name: str,
    log_file: str = "utils/scanner.log",
    level=LOG_LEVEL,
    max_bytes: int = 1024 * 1024 * 5,
    backup_count: int = 6,
):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    # IMPORTANT:
    # We still avoid duplicate handlers per logger name.
    if not logger.handlers:
        qh = _ensure_listener(log_file, level, max_bytes, backup_count)
        qh.setLevel(level)
        logger.addHandler(qh)

    return logger


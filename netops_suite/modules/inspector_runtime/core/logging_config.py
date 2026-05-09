import logging
import os
from datetime import datetime

try:
    from colorama import Fore, Style, init as colorama_init
except Exception:
    Fore = None
    Style = None
    colorama_init = None


class ColorFormatter(logging.Formatter):
    def __init__(self, fmt: str, enable_color: bool = True) -> None:
        super().__init__(fmt)
        self.enable_color = enable_color and Fore is not None and Style is not None

    def format(self, record: logging.LogRecord) -> str:
        original_levelname = record.levelname
        if self.enable_color:
            color = {
                logging.DEBUG: Fore.CYAN,
                logging.INFO: Fore.GREEN,
                logging.WARNING: Fore.YELLOW,
                logging.ERROR: Fore.RED,
                logging.CRITICAL: Fore.RED + Style.BRIGHT,
            }.get(record.levelno, "")
            record.levelname = f"{color}{original_levelname}{Style.RESET_ALL}"
        message = super().format(record)
        record.levelname = original_levelname
        return message


def init_logging(
    run_timestamp: str | None = None,
    log_dir: str = "logs",
    enable_console: bool = True,
    file_level: int = logging.DEBUG,
    console_level: int = logging.WARNING,
    enable_color: bool = True,
) -> str:
    """
    Initialize application-wide logging.

    - Attach handlers only to the root logger to avoid duplicated output.
    - Use the same format for file and console logs.

    Returns
    -------
    str
        Path of the created log file.
    """
    os.makedirs(log_dir, exist_ok=True)

    timestamp = run_timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = os.path.join(log_dir, f"netops_inspector_{timestamp}.log")

    root_logger = logging.getLogger()

    # Clear existing handlers first.
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Keep root logger at DEBUG. Handlers control actual output level.
    root_logger.setLevel(logging.DEBUG)

    log_format = "%(asctime)s | [%(threadName)s] | %(levelname)s | %(message)s"
    formatter = logging.Formatter(log_format)

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(file_level)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    if enable_console:
        if colorama_init is not None:
            colorama_init(autoreset=True)
        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_level)
        console_handler.setFormatter(ColorFormatter(log_format, enable_color=enable_color))
        root_logger.addHandler(console_handler)

    logging.getLogger("paramiko").setLevel(logging.WARNING)
    logging.getLogger("netmiko").setLevel(logging.WARNING)

    root_logger.debug("Global logging initialized")
    root_logger.debug("Log file path: %s", log_file)

    return log_file

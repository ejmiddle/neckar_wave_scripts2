import logging
import logging.config
from pathlib import Path


def _configure_logging() -> None:
    log_conf_path = Path(__file__).resolve().parent.parent / "logging.conf"
    data_log_dir = Path(__file__).resolve().parent.parent / "data" / "logs"
    fallback_log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir = data_log_dir if data_log_dir.exists() else fallback_log_dir
    if not log_dir.exists():
        log_dir = data_log_dir
    log_dir.mkdir(parents=True, exist_ok=True)

    if log_conf_path.exists():
        logging.config.fileConfig(
            log_conf_path,
            disable_existing_loggers=False,
            defaults={"logdirpath": str(log_dir)},
        )
    else:
        logging.basicConfig(level=logging.INFO)


_configure_logging()

# Default app logger
logger = logging.getLogger("neckarwave")

import logging
import logging.config
from pathlib import Path


def _configure_logging() -> None:
    log_conf_path = Path(__file__).resolve().parent.parent / "logging.conf"
    log_dir = Path(__file__).resolve().parent.parent / "logs"
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

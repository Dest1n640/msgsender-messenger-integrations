"""Заглушка pydci_logging для локального тестирования вне Docker-контейнера."""
import logging


def init_logger(log_file: str, level: int = logging.WARNING) -> None:
    """Минимальная инициализация логгера: только stderr, без файла."""
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )

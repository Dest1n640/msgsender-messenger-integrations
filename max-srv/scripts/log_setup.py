import os
import sys
import logging
from logging.handlers import TimedRotatingFileHandler

LOG_FILE = os.getenv("LOG_FILE", "/var/log/max-srv.log")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_DAYS = int(os.getenv("LOG_DAYS", "3"))
LOG_MAX_MB = int(os.getenv("LOG_MAX_MB", "50"))


class TimedAndSizedHandler(TimedRotatingFileHandler):
    """Ротация лога по времени с дополнительным
    ограничением по размеру файла. Приоритеты:
      1. время  — файл ротируется каждую ночь в 00:00
      2. размер — если файл достиг LOG_MAX_MB раньше полуночи, ротация
                  происходит досрочно
    Хранит не более LOG_DAYS архивов; старые удаляются автоматически.
    """

    def __init__(self, filename: str, max_bytes: int, **kwargs):
        super().__init__(filename, **kwargs)
        self.max_bytes = max_bytes

    def shouldRollover(self, record) -> bool:  # type: ignore[override]
        if self.stream and self.stream.tell() >= self.max_bytes:
            return True
        return super().shouldRollover(record)


def setup_logging() -> None:
    """Настраивает корневой логгер приложения.

    Параметры задаются через переменные окружения:
      LOG_FILE    — путь к файлу лога        (default: /var/log/max-srv.log)
      LOG_LEVEL   — уровень: DEBUG/INFO/...  (default: INFO)
      LOG_DAYS    — сколько дней хранить     (default: 3)
      LOG_MAX_MB  — макс. размер файла в МБ  (default: 50)
    """
    level = getattr(logging, LOG_LEVEL.upper(), logging.INFO)

    file_handler = TimedAndSizedHandler(
        LOG_FILE,
        max_bytes=LOG_MAX_MB * 1024 * 1024,
        when="midnight",
        backupCount=LOG_DAYS,
        encoding="utf-8",
    )

    logging.basicConfig(
        level=level,
        format="%(asctime)s [pid=%(process)d] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S.%f",
        handlers=[
            file_handler,
            logging.StreamHandler(sys.stdout),
        ],
        force=True,
    )

    logging.info(
        f"Logging configured: level={LOG_LEVEL.upper()}, "
        f"file={LOG_FILE}, retain={LOG_DAYS} days, max_size={LOG_MAX_MB} MB"
    )

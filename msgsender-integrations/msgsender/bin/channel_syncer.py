#!/usr/bin/python3
"""Сервис для синхронизации каналов в настройках уведомлений с помощью нотификаций"""

import logging
from asyncio import get_event_loop
from contextlib import suppress
from typing import Any, Dict, Iterable

from pydci_isp_client import Client, InternalService
from pydci_logging import init_logger
from pydci_notifier import InternalNotifierV4

LOG_FILE_PATH = "/var/log/channel_syncer.log"


def make_alert_client() -> Client:
    """Клиент alert settings"""

    return Client(InternalService(name="alert"))


def make_msgsender_client() -> Client:
    """Клиент message sender"""

    return Client(InternalService(name="msgsender", instance_id="auth"))


def get_msgs_telegram_channels() -> list[int]:
    """Поулчаем список телеграм каналов из msgsender"""

    resp = make_msgsender_client().get("channel?where=delivery_method+EQ+'telegram'")
    resp.raise_for_status()
    channels: list[Dict[str, Any]] = resp.json()["list"]
    return [channel.get("id", 0) for channel in channels]


def get_msgs_slack_channels() -> list[int]:
    """Получаем список slack каналов из msgsender"""

    resp = make_msgsender_client().get("channel?where=delivery_method+EQ+'slack'")
    resp.raise_for_status()
    channels: list[Dict[str, Any]] = resp.json()["list"]
    return [channel.get("id", 0) for channel in channels]


def get_msgs_max_channels() -> list[int]:
    """Получаем список max каналов из msgsender"""

    resp = make_msgsender_client().get("channel?where=delivery_method+EQ+'max'")
    resp.raise_for_status()
    channels: list[Dict[str, Any]] = resp.json()["list"]
    return [channel.get("id", 0) for channel in channels]


def get_alert_settings() -> Iterable[Dict[str, Any]]:
    """Возвращает список настроек"""

    resp = make_alert_client().get("setting")
    resp.raise_for_status()
    settings: list[Dict[str, Any]] = resp.json()["list"]
    if not settings:
        return

    for setting in settings:
        yield setting


def sync_channels() -> None:
    """Актуализируем список каналов в настройках alert_settings"""

    alert_client = make_alert_client()
    msgs_telegram_channels = set(get_msgs_telegram_channels())
    msgs_slack_channels = set(get_msgs_slack_channels())
    msgs_max_channels = set(get_msgs_max_channels())
    for setting in get_alert_settings():
        setting_id = setting.get("id", 0)
        channels_in_settings: Dict[str, Any] = setting.get("channel")
        is_changed = False

        if telegram_channels := channels_in_settings.get("telegram", []):
            valid_channels = [c for c in telegram_channels if c in msgs_telegram_channels]
            if len(valid_channels) != len(telegram_channels):
                channels_in_settings["telegram"] = valid_channels
                is_changed = True

        if slack_channels := channels_in_settings.get("slack", []):
            valid_channels = [c for c in slack_channels if c in msgs_slack_channels]
            if len(valid_channels) != len(slack_channels):
                channels_in_settings["slack"] = valid_channels
                is_changed = True

        if max_channels := channels_in_settings.get("max", []):
            valid_channels = [c for c in max_channels if c in msgs_max_channels]
            if len(valid_channels) != len(max_channels):
                channels_in_settings["max"] = valid_channels
                is_changed = True

        if is_changed:
            alert_client.post(
                f"setting/{setting_id}", params={"channel": channels_in_settings}
            ).raise_for_status()


def settings_delete_channel(channel_id: int, delivery_method: str = "telegram") -> None:
    """Delete channel in alert_settings"""

    logging.info(f"Got channel with {channel_id} id for delete (method={delivery_method})")

    alert_client = make_alert_client()
    for setting in get_alert_settings():
        setting_id = setting.get("id", 0)
        channels_in_settings: Dict[str, Any] = setting.get("channel")

        if method_channels := channels_in_settings.get(delivery_method, []):
            if channel_id in method_channels:
                method_channels.remove(channel_id)
                alert_client.post(
                    f"setting/{setting_id}", params={"channel": channels_in_settings}
                ).raise_for_status()


async def on_channel_delete(notification: Dict[str, Any]) -> None:
    """Обработка нотификации на удаление канала"""

    with suppress(Exception):
        if notification["id"]:
            delivery_method = notification.get("data", {}).get("delivery_method", "telegram")
            settings_delete_channel(notification["id"], delivery_method)


def main() -> None:
    """Main."""

    sync_channels()

    notifier = InternalNotifierV4()
    entity = notifier.add_entity("channel")
    entity.on_delete(on_channel_delete)

    get_event_loop().run_until_complete(notifier.watch())


if __name__ == "__main__":
    init_logger(LOG_FILE_PATH)

    try:
        main()
    except Exception as error:
        logging.exception(error)
        raise

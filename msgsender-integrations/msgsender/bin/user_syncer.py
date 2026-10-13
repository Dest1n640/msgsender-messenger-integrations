#!/usr/bin/python3
"""Сервис для синхронизации пользователей с авторизации с помощью нотификаций"""

import logging
from typing import Iterable, Dict, Any
from os import getenv
from pprint import pformat
from asyncio import get_event_loop
from itertools import count
from functools import lru_cache
from contextlib import suppress

from pydci_logging import init_logger
from pydci_isp_client import InternalService, Client
from pydci_notifier import InternalNotifierV4


LOG_FILE_PATH = "/var/log/msgs_user_syncer.log"
DEFAULT_USER_LIMIT = 100
NOTIFIER_SERVICE = getenv("NOTIFIER_ADDRESS", getenv("PROXY_SERVICE", "localhost:1500"))


def make_auth_client() -> Client:
    """Клиент для Auth v4"""

    return Client(InternalService(name="auth", instance_id="auth", api_version="v4"))


@lru_cache(maxsize=1)
def make_msgs_client() -> Client:
    """Клиент для message sender-а"""

    return Client(InternalService(name="msgsender", instance_id="auth"))


def msgs_add_user(user: Dict[str, Any]) -> None:
    """Add/edit user to message sender."""

    logging.info("Got user for sync")
    logging.debug(pformat(user))
    msgs = make_msgs_client()
    msgs.post(
        f"user/{user['id']}",
        params={"language": user["lang"], "delivery_method": {"name": "email", "params": {"email": user["email"]}}},
    ).raise_for_status()


def msgs_delete_user(user_id: int) -> None:
    """Delete user in message sender."""

    logging.info(f"Got user with {user_id} id for delete")
    msgs = make_msgs_client()
    msgs.delete(f"user/{user_id}").raise_for_status()


def get_auth_users() -> Iterable[Dict[str, Any]]:
    """Поулчаем список пользователей из авторизации чанками."""
    auth = make_auth_client()
    for offset in count(0, DEFAULT_USER_LIMIT):
        resp = auth.get(f"user?limit={offset},{DEFAULT_USER_LIMIT}")
        resp.raise_for_status()
        users = resp.json()["list"]
        if not users:
            break

        for user in users:
            yield user


async def on_user_create(notification: Dict[str, Any]) -> None:
    """Экшен на создание/редактирование пользователя."""

    with suppress(Exception):
        if notification["data"]["list"]:
            msgs_add_user(notification["data"]["list"][0])

async def on_user_delete(notification: Dict[str, Any]) -> None:
    """Экшен на удаление пользователя."""

    with suppress(Exception):
        if notification["id"]:
            msgs_delete_user(notification["id"])


on_user_update = on_user_create


def main() -> None:
    """Main."""
    # запускаем нотификатор для синхронизации
    notifier = InternalNotifierV4()
    entity = notifier.add_entity("user")
    entity.on_create(on_user_create, "auth/v4/user?where=id+EQ+{user_id}")
    entity.on_update(on_user_update, "auth/v4/user?where=id+EQ+{user_id}")
    entity.on_delete(on_user_delete)

    get_event_loop().run_until_complete(notifier.watch())


if __name__ == "__main__":
    init_logger(LOG_FILE_PATH)
    main()

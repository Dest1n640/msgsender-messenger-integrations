#!/usr/bin/env python3
"""
Интерактивный тестовый скрипт для отправки тестовых сообщений и шаблонов через bin/max.
"""

import json
import os
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_MAX_PATH = os.path.join(SCRIPT_DIR, "bin", "max")
CONFIG_PATH = os.path.join(SCRIPT_DIR, ".test_max_chat_id")
TEMPLATES_DIR = os.path.join(SCRIPT_DIR, "etc", "template")

UNIVERSAL_MOCK_PARAMS = {
    "language": "ru",
    "resolved": False,
    "task": {
        "status": "failed",
        "name": "Проверка резервного копирования",
        "entity": "node"
    },
    "entity": {
        "node": {
            "id": "node-101",
            "name": "node-01.cluster.local",
            "ip": "192.168.1.50"
        },
        "host": {
            "id": "host-202",
            "name": "vm-web-server",
            "ip": "192.168.1.100"
        },
        "server": {
            "id": "srv-303",
            "name": "srv-rack1-u4",
            "ip": "10.0.0.10"
        },
        "extra": {
            "rack": {"name": "Стойка A1"},
            "unit": {"end_date": "2030-12-31"},
            "slot": 1,
            "chassis": {"name": "Шасси C1"},
            "platform": {"size": 2},
            "location_info": {"name": "ЦОД Москва-1"},
            "location_name": "ЦОД Москва-1",
            "rack_name": "Стойка A1",
            "rack_size": 42,
            "rack_unit": 12,
            "entity_id": "1",
            "entity_name": "Сервер №1",
            "server": {
                "rack_name": "Стойка A1",
                "unit": 12,
                "platform_size": 2,
                "location_name": "ЦОД Москва-1",
                "slot": 1,
                "chassis_name": "Шасси C1"
            }
        }
    },
    "alert": {
        "type": "system_health",
        "custom_name": "Превышение порога",
        "custom_text": "Температура выше 80°C",
        "value": 85.5,
        "time": 300,
        "time_period": 5,
        "threshold_gt": 80
    },
    "message": {
        "last_val": 85.5,
        "time": 300,
        "last_value": 85.5,
        "lower_threshold": 20,
        "higher_threshold": 75,
        "sensor_name": "CPU Temp Sensor",
        "server_component": {
            "name": "Intel Xeon E5-2680",
            "serial_number": "SN-987654321",
            "health": "warning"
        },
        "product_info": {"url": "https://dci.example.com/"},
        "server_sensor": {"name": "CPU Temp Sensor"},
        "diag_operation": {"name": "Полная диагностика RAM"},
        "diag_error": [
            {"type": "smart"},
            {"type": "local_speed"}
        ],
        "server_profile": {"name": "Профиль High performance"},
        "unit_reservation": {"id": 101},
        "warehouse_reservation": {"id": 202},
        "components_binding": {"id": 303},
        "warehouse_warranty": {"id": 404},
        "dss_chassis": {"name": "Chassis-DSS-01"},
        "component": {
            "ids": [1, 2, 3],
            "barcodes": ["BC-1001", "BC-1002"],
            "entity": "server",
            "warehouse_name": "Основной склад",
            "end_date": "2030-12-31",
            "date_shift": 5,
            "identical_date": True,
            "size": 2,
            "unit": 12,
            "warehouse_state": "in_rack",
            "location_name": "ЦОД Москва-1",
            "rack_name": "Стойка A1"
        }
    },
    "status": {"value": "unavailable"},
    "timesync": {"value": "unavailable"}
}


def load_chat_id() -> str | None:
    """Загружает сохраненный Chat ID из файла конфигурации."""
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                chat_id = f.read().strip()
                if chat_id:
                    return chat_id
        except Exception as e:
            print(f"Предупреждение: Не удалось прочитать файл конфигурации: {e}")
    return None


def save_chat_id(chat_id: str) -> None:
    """Сохраняет Chat ID в файл конфигурации."""
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write(str(chat_id).strip())
    except Exception as e:
        print(f"Ошибка при сохранении Chat ID: {e}")


def prompt_chat_id(prompt_text: str = "Введите Chat ID: ") -> str:
    """Запрашивает Chat ID у пользователя."""
    while True:
        try:
            val = input(prompt_text).strip()
            if val:
                return val
            print("Chat ID не может быть пустым. Попробуйте еще раз.")
        except (KeyboardInterrupt, EOFError):
            print("\nПрервано пользователем.")
            sys.exit(0)


def parse_chat_id_value(chat_id_str: str):
    """Преобразует строку Chat ID в число, если это возможно, либо оставляет строкой."""
    if chat_id_str.lstrip("-").isdigit():
        return int(chat_id_str)
    return chat_id_str


def execute_bin_max(payload: dict) -> None:
    """Отправляет переданный payload в bin/max."""
    if not os.path.exists(BIN_MAX_PATH):
        print(f"Ошибка: Исполняемый файл не найден по пути {BIN_MAX_PATH}")
        return

    json_data = json.dumps(payload, ensure_ascii=False)
    print("\n--- Отправляемый JSON ---")
    print(json.dumps(payload, indent=2, ensure_ascii=False))

    env = os.environ.copy()
    if "MAX_SRV_HOST" not in env:
        env["MAX_SRV_HOST"] = "max-srv:8080"
        print(f"[Установлена переменная окружения MAX_SRV_HOST={env['MAX_SRV_HOST']}]")
    else:
        print(f"[Используется переменная окружения MAX_SRV_HOST={env['MAX_SRV_HOST']}]")

    print("\n--- Вызов bin/max ---")
    try:
        process = subprocess.Popen(
            [sys.executable, BIN_MAX_PATH],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True
        )
        stdout, stderr = process.communicate(input=json_data)

        if stdout:
            print(f"STDOUT:\n{stdout}")
        if stderr:
            print(f"STDERR:\n{stderr}")

        print(f"Код возврата: {process.returncode}")
        print("---------------------")
    except Exception as e:
        print(f"Ошибка при вызове bin/max: {e}")


def send_test_message(chat_id_str: str) -> None:
    """Запрашивает текст сообщения (нажатие Enter выбирает текст по умолчанию) и отправляет его."""
    try:
        text = input("Введите текст сообщения (нажмите Enter для текста по умолчанию): ").strip()
    except (KeyboardInterrupt, EOFError):
        print("\nОтмена.")
        return

    if not text:
        text = "Тестовое сообщение"
        print(f"Используется текст по умолчанию: '{text}'")

    chat_id = parse_chat_id_value(chat_id_str)
    payload = {
        "stdin": {
            "chat_id": chat_id,
            "msg": {
                "language": "ru",
                "text": text
            },
            "template": {
                "body": "Тестовое сообщение MAX: {{ params.text }}"
            }
        }
    }
    execute_bin_max(payload)


def get_available_templates() -> list[tuple[str, str]]:
    """Находит все шаблоны MAX (*.mf в подпапках max)."""
    templates = []
    if os.path.exists(TEMPLATES_DIR):
        for root, _, files in os.walk(TEMPLATES_DIR):
            for file in sorted(files):
                if file.endswith(".mf") and os.path.basename(root) == "max":
                    full_path = os.path.join(root, file)
                    rel_path = os.path.relpath(full_path, TEMPLATES_DIR)
                    templates.append((rel_path, full_path))
    templates.sort(key=lambda x: x[0])
    return templates


def templates_menu(chat_id_str: str) -> None:
    """Раздел с выбором и отправкой любого из доступных шаблонов MAX."""
    templates = get_available_templates()

    if not templates:
        print(f"\nОшибка: Шаблоны не найдены в каталоге {TEMPLATES_DIR}")
        return

    while True:
        print("\n" + "=" * 55)
        print("  РАЗДЕЛ ШАБЛОНОВ MAX")
        print("=" * 55)
        print(" Выберите шаблон для отправки:\n")

        for idx, (rel_path, _) in enumerate(templates, 1):
            print(f"  [{idx:2d}] {rel_path}")

        print("\n  [ 0] Вернуться в главное меню")
        print("=" * 55)

        try:
            choice = input(f"Выберите шаблон [0-{len(templates)}]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nВозврат в главное меню...")
            break

        if choice == "0":
            break

        if choice.isdigit() and 1 <= int(choice) <= len(templates):
            selected_rel, selected_full = templates[int(choice) - 1]

            try:
                lang = input("Введите язык (ru/en, по умолчанию ru): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                print("\nВозврат...")
                continue

            if lang not in ("ru", "en"):
                lang = "ru"

            try:
                with open(selected_full, "r", encoding="utf-8") as f:
                    template_body = f.read()
            except Exception as e:
                print(f"Ошибка при чтении шаблона {selected_full}: {e}")
                continue

            chat_id = parse_chat_id_value(chat_id_str)
            msg_params = {**UNIVERSAL_MOCK_PARAMS, "language": lang}

            payload = {
                "stdin": {
                    "chat_id": chat_id,
                    "msg": msg_params,
                    "template": {
                        "body": template_body
                    }
                }
            }

            print(f"\n>>> Отправка шаблона: {selected_rel} [язык: {lang}]")
            execute_bin_max(payload)
        else:
            print("Неверный выбор. Попробуйте еще раз.")


def main() -> None:
    chat_id = load_chat_id()

    if not chat_id:
        print("Первый запуск: необходимо ввести Chat ID.")
        chat_id = prompt_chat_id()
        save_chat_id(chat_id)

    while True:
        print("\n" + "=" * 55)
        print(" Интерактивное меню тестирования адаптера MAX")
        print(f" Текущий Chat ID: {chat_id}")
        print("=" * 55)
        print("1. Отправить тестовое сообщение")
        print("2. Отправить заготовленный шаблон MAX (раздел шаблонов)")
        print("3. Изменить chat-id")
        print("4. Выход")
        print("=" * 55)

        try:
            choice = input("Выберите действие [1-4]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nВыход...")
            break

        if choice == "1":
            send_test_message(chat_id)
        elif choice == "2":
            templates_menu(chat_id)
        elif choice == "3":
            chat_id = prompt_chat_id("Введите новый Chat ID: ")
            save_chat_id(chat_id)
            print(f"Chat ID успешно изменен на: {chat_id}")
        elif choice == "4":
            print("Выход...")
            break
        else:
            print("Неверный выбор. Пожалуйста, введите число от 1 до 4.")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3

"""
Интерактивный тестовый скрипт для проверки интеграции со Slack.

Блок 1 (обязательный): Проверка доставки — запускает реальный slack-скрипт
через subprocess и тестирует интеграцию с реальным slack-srv (без моков).

Блок 2 (опциональный): Проверка рендеринга — динамически загружает боевой скрипт
и рендерит через него Jinja2-шаблоны для визуальной проверки в канале.

Запуск внутри контейнера (основной сценарий):
    docker exec -it dev-msgsender python3 /opt/ispsystem/msgsender/test_slack.py

Запуск локально через uv (рекомендуемый):
    uv run msgsender/test_slack.py
"""

import argparse
import gettext
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
import requests
import importlib.util
import importlib.machinery

SCRIPT_DIR     = Path(__file__).parent.resolve()   # /opt/ispsystem/msgsender  (внутри контейнера)
SLACK_SCRIPT   = SCRIPT_DIR / "bin" / "slack"
TEMPLATES_PATH = SCRIPT_DIR / "etc" / "template"

# При локальном запуске _stubs/ находятся на уровень выше msgsender/ (в корне репо).
# Внутри контейнера этой папки нет — пакет установлен системно.
_REPO_ROOT = SCRIPT_DIR.parent
_STUBS_DIR = _REPO_ROOT / "_stubs"

# Если мы запускаем локально вне Докера, микросервис доступен на localhost:8080.
# Внутри Докера он доступен по hostname slack-srv:8080.
_IS_LOCAL = _STUBS_DIR.exists()
SLACK_SRV_HOST = os.getenv("SLACK_SRV_HOST", "127.0.0.1:8080" if _IS_LOCAL else "slack-srv:8080")

# Убеждаемся, что pydci_logging доступен для динамического импорта bin/slack
if _IS_LOCAL and str(_STUBS_DIR) not in sys.path:
    sys.path.insert(0, str(_STUBS_DIR))

# Динамически загружаем bin/slack как модуль
loader = importlib.machinery.SourceFileLoader("slack_cli", str(SLACK_SCRIPT))
spec = importlib.util.spec_from_file_location("slack_cli", SLACK_SCRIPT, loader=loader)
slack_cli = importlib.util.module_from_spec(spec)
spec.loader.exec_module(slack_cli)

# Патчим путь к шаблонам по умолчанию для запуска вне докера,
# чтобы избежать падения при поиске include шаблонов.
if not Path("/opt/ispsystem/msgsender/etc/template").exists():
    slack_cli.setup_environment.__defaults__ = (str(TEMPLATES_PATH),)


def ok(msg: str) -> None:
    """Печатает строку PASS."""
    print(f"  PASS  {msg}")


def fail(msg: str, detail: str = "") -> None:
    """Печатает строку FAIL с опциональной детализацией."""
    print(f"  FAIL  {msg}")
    if detail:
        print(f"        {detail}")


def info(msg: str) -> None:
    """Печатает информационное сообщение."""
    print(f"  INFO  {msg}")


def header(title: str) -> None:
    """Печатает заголовок раздела."""
    print()
    print(f"--- {title} ---")


def section(title: str) -> None:
    """Печатает подзаголовок группы тест-кейсов."""
    print(f"\n  {title}")



class TestResult:
    """Контейнер результата одного тест-кейса."""

    def __init__(self, name: str, passed: bool, detail: str = ""):
        self.name   = name
        self.passed = passed
        self.detail = detail





def _build_extra_pythonpath() -> list[str]:
    """
    Возвращает список дополнительных путей для PYTHONPATH subprocess.

    Внутри контейнера: pydci_logging — системный пакет.
    Вне контейнера (локально): добавляет _stubs/ (pydci_logging).
    Зависимости (jinja2, requests) подтянутся автоматически через uv (VIRTUAL_ENV/PYTHONPATH).
    """
    extra: list[str] = []
    if _IS_LOCAL and _STUBS_DIR.exists():
        extra.append(str(_STUBS_DIR))
    return extra


def run_slack(payload: dict) -> tuple[int, str, str]:
    """
    Запускает bin/slack как отдельный процесс, передавая payload через stdin.

    Возвращает: (exit_code, stdout, stderr)
    """
    stdin_data = json.dumps(payload).encode()

    env = os.environ.copy()
    env["SLACK_SRV_HOST"] = SLACK_SRV_HOST  # Передаем хост микросервиса в bin/slack
    
    extra = _build_extra_pythonpath()
    if extra:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = os.pathsep.join(extra + ([existing] if existing else []))

    result = subprocess.run(
        [sys.executable, str(SLACK_SCRIPT)],
        input=stdin_data,
        capture_output=True,
        cwd=str(SCRIPT_DIR),
        env=env,
    )
    return (
        result.returncode,
        result.stdout.decode(errors="replace").strip(),
        result.stderr.decode(errors="replace").strip(),
    )


def _build_payload(webhook_url: str, template_body: str, msg: dict) -> dict:
    """
    Формирует JSON-структуру, которую slack-скрипт ожидает на stdin.

    Slack-скрипт читает: json.load(sys.stdin)["stdin"]["webhook_url"] и т.д.
    """
    return {
        "stdin": {
            "webhook_url": webhook_url,
            "template": {"body": template_body},
            "msg": msg,
        }
    }


SIMPLE_TEMPLATE = "Test message from {{ params.product }}"

MINIMAL_MSG = {"language": "en"}
FULL_MSG    = {"language": "en", "product": "ISPsystem", "version": "6.0", "env": "staging"}
PARTIAL_MSG = {"language": "en", "product": "ISPsystem"}


def _delivery_case(
    name: str,
    webhook_url: str,
    template_body: str,
    msg: dict,
    expected_exit: int,
) -> TestResult:
    """
    Выполняет один тест-кейс Блока 1: запускает slack-скрипт и сравнивает
    фактический exit-code с ожидаемым.
    """
    payload = _build_payload(webhook_url, template_body, msg)
    exit_code, stdout, stderr = run_slack(payload)

    if exit_code == expected_exit:
        ok(f"{name} (exit {exit_code})")
        return TestResult(name, True)
    else:
        detail = f"stdout: {stdout!r}" if stdout else ""
        if stderr:
            detail += f"  stderr: {stderr[:120]!r}"
        fail(
            f"{name} (ожидался exit {expected_exit}, получен {exit_code})",
            detail,
        )
        return TestResult(name, False, detail)


def run_delivery_tests(webhook_url: str) -> list[TestResult]:
    """Запускает все тест-кейсы Блока 1 и возвращает список результатов."""
    results: list[TestResult] = []

    header("БЛОК 1: Проверка доставки сообщений (через slack-srv)")

    # Так как мы больше не используем моки, мы проверяем реальную доставку
    # через slack-srv на предоставленный webhook.
    
    section("Успешная доставка (E2E через slack-srv)")
    results.append(_delivery_case(
        "Полный набор полей",
        webhook_url, SIMPLE_TEMPLATE, FULL_MSG,
        expected_exit=0,
    ))

    section("Доставка с частичным набором полей")
    results.append(_delivery_case(
        "Частичный набор полей",
        webhook_url, SIMPLE_TEMPLATE, PARTIAL_MSG,
        expected_exit=0,
    ))

    section("Доставка с минимальными полями")
    results.append(_delivery_case(
        "Только язык",
        webhook_url, SIMPLE_TEMPLATE, MINIMAL_MSG,
        expected_exit=0,
    ))

    section("Проверка Rate Limiting (отправка нескольких сообщений за секунду)")
    # Slack имеет лимит 1 сообщение в секунду на канал. 
    # Отправляем 3 сообщения подряд без пауз, чтобы гарантированно 
    # спровоцировать 429 ошибку внутри slack-srv. 
    # Бинарник bin/slack должен отработать без ошибок (exit 0), 
    # так как slack-srv заберет это в очередь и сам обработает ретраи.
    for i in range(1, 4):
        msg_copy = FULL_MSG.copy()
        msg_copy["product"] = f"Rate Limit Test {i}/3"
        results.append(_delivery_case(
            f"Быстрая отправка {i} из 3",
            webhook_url, SIMPLE_TEMPLATE, msg_copy,
            expected_exit=0,
        ))

    return results



def _render_template(template_path: str, params: dict, lang: str) -> tuple[bool, str, str]:
    """
    Рендерит шаблон по относительному пути внутри TEMPLATES_PATH.

    Возвращает: (успех, результат рендеринга, сообщение об ошибке)
    """
    try:
        # Читаем исходный файл шаблона с диска
        with open(TEMPLATES_PATH / template_path, "r", encoding="utf-8") as f:
            template_body = f.read()
        
        # Формируем Payload в том виде, в каком его передает основной msgsender
        template_payload = {"body": template_body}
        
        # Вызываем оригинальный класс Message из импортированного скрипта
        message = slack_cli.Message(template_payload, params)
        rendered = message.render()
        
        return True, rendered, ""
    except Exception as exc:
        return False, "", str(exc)





UNIVERSAL_MOCK_PARAMS = {
    "task": {"status": "failed", "name": "mock-task", "entity": "node"},
    "entity": {
        "node": {"id": "mock-node", "name": "mock-node.local", "ip": "10.0.0.1"},
        "host": {"id": "mock-host", "name": "mock-host", "ip": "10.0.0.2"},
        "server": {"id": "mock-srv", "name": "mock-server", "ip": "10.0.0.3"},
        "extra": {
            "rack": {"name": "Mock Rack"},
            "unit": {"end_date": "2030-01-01"},
            "slot": None,
            "platform": {"size": 1},
            "location_info": {"name": "Mock Location"},
            "location_name": "Mock Location",
            "rack_name": "Mock Rack",
            "rack_size": 1,
            "rack_unit": 42,
            "entity_id": "1",
            "entity_name": "Mock Entity",
            "server": {
                "rack_name": "Mock Rack",
                "unit": 42,
                "platform_size": 1,
                "location_name": "Mock Location",
            },
        },
    },
    "alert": {
        "type": "system_health",
        "value": 99.9,
        "time": 600,
        "time_period": 10,
        "threshold_gt": 90,
    },
    "message": {
        "last_val": 95,
        "time": 300,
        "last_value": 95,
        "lower_threshold": 10,
        "higher_threshold": 90,
        "sensor_name": "Mock Sensor",
        "server_component": {"name": "Mock Component", "serial_number": "SN-123", "health": "warning"},
        "product_info": {"url": "https://dci.mock.local/"},
        "server_sensor": {"name": "Mock Sensor"},
        "diag_operation": {"name": "Mock Diag"},
        "server_profile": {"name": "Mock Profile"},
        "unit_reservation": {"id": 1},
        "warehouse_reservation": {"id": 1},
        "components_binding": {"id": 1},
        "warehouse_warranty": {"id": 1},
        "dss_chassis": {"name": "Mock Chassis"},
        "component": {
            "ids": [1, 2, 3],
            "barcodes": ["12345", "67890"],
            "entity": "server",
            "warehouse_name": "Mock Warehouse",
            "end_date": "2030-01-01",
            "date_shift": 5,
            "identical_date": True,
            "size": 1,
            "unit": 42,
            "warehouse_state": "in_rack",
            "location_name": "Mock Location",
            "rack_name": "Mock Rack"
        }
    },
    "resolved": False,
    "status": {"value": "mock-status"},
}

def run_template_tests(webhook_url: str | None = None) -> list[TestResult]:
    """
    Запускает Блок 2:
    Ищет все slack шаблоны, рендерит их в памяти (на en, ru, es).
    Если передан webhook_url, отправляет русскую версию (ru) в Mattermost
    для визуального (E2E) тестирования.
    """
    results: list[TestResult] = []

    header("БЛОК 2: Проверка рендеринга шаблонов")
    
    if webhook_url:
        info(f"Включен E2E режим. Шаблоны (ru) будут отправлены на webhook: {webhook_url}")

    # Автоматически ищем все .mf шаблоны в подпапках slack
    templates = list(TEMPLATES_PATH.rglob("slack/*.mf"))
    if not templates:
        fail("Не найдено ни одного шаблона slack/*.mf")
        return [TestResult("find_templates", False, "no templates found")]
    
    info(f"Найдено шаблонов: {len(templates)}")

    # Чтобы не отправлять по 3 сообщения на шаблон в визуальном режиме,
    # мы отправляем только русский вариант. Но рендерим все три, чтобы
    # убедиться в отсутствии синтаксических ошибок.
    langs_to_check = ("en", "ru", "es")

    for tmpl_path in sorted(templates):
        # Вычисляем относительный путь (например, vm/slack/alert_node.mf)
        rel_path = str(tmpl_path.relative_to(TEMPLATES_PATH))
        section(f"Шаблон: {rel_path}")

        for lang in langs_to_check:
            params = {**UNIVERSAL_MOCK_PARAMS, "language": lang}
            case_name = f"{rel_path} [{lang}]"
            
            # Проверяем успешность рендера (без отправки)
            success, rendered, error = _render_template(rel_path, params, lang)
            
            if not success:
                fail(f"{case_name} -- ошибка рендеринга", error[:120])
                results.append(TestResult(case_name, False, error))
                continue
            
            if not rendered.strip():
                fail(f"{case_name} -- пустой результат")
                results.append(TestResult(case_name, False, "empty output"))
                continue
            
            ok(f"{case_name} отрендерен ({len(rendered)} символов)")
            results.append(TestResult(case_name, True))

            # Если включен режим отправки и язык русский - шлём в webhook через slack-srv
            if webhook_url and lang == "ru":
                try:
                    payload = {"webhook": webhook_url, "text": rendered}
                    slack_srv_url = f"http://{SLACK_SRV_HOST}/sendMsg"
                    
                    resp = requests.post(slack_srv_url, json=payload, timeout=5)
                    if resp.status_code in (200, 202):
                        ok(f"  -> Отправлено в slack-srv (HTTP {resp.status_code})")
                    else:
                        fail(f"  -> Ошибка от slack-srv (HTTP {resp.status_code})", resp.text[:100])
                except Exception as e:
                    fail(f"  -> Исключение при обращении к slack-srv", str(e))

    return results



def print_summary(results: list[TestResult]) -> None:
    """Печатает итоговый счёт и список провалившихся тестов."""
    total  = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    header("ИТОГ")
    print(f"\n  Всего  : {total}")
    print(f"  Прошло : {passed}")
    if failed:
        print(f"  Упало  : {failed}")
        print()
        print("  Провалившиеся тесты:")
        for r in results:
            if not r.passed:
                print(f"    - {r.name}")
    else:
        print("\n  Все тесты прошли.")
    print()



def interactive_menu() -> tuple[str, str]:
    """
    Показывает меню в консоли и возвращает (режим, webhook_url).
    Режим: 'delivery' | 'templates' | 'all'
    """
    print("\nТестирование интеграции msgsender <-> Slack\n")
    print("  Выберите, какие тесты запустить:\n")
    print("    [1]  Блок 1 -- Проверка доставки сообщений (через slack-srv)")
    print("    [2]  Блок 2 -- Проверка рендеринга шаблонов Jinja2 (E2E визуализация)")
    print("    [3]  Оба блока\n")

    mode = "all"
    while True:
        try:
            choice = input("  Ваш выбор [1/2/3]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nПрервано.")
            sys.exit(0)
            
        if choice == "1":
            mode = "delivery"
            break
        elif choice == "2":
            mode = "templates"
            break
        elif choice == "3":
            mode = "all"
            break
        print("  Введите 1, 2 или 3.")

    webhook_url = ""
    while not webhook_url:
        print("\n  Введите Webhook URL тестового канала Slack/Mattermost.")
        print("  Он обязателен, так как тесты теперь не используют моки.\n")
        try:
            url_input = input("  Webhook URL: ").strip()
            if url_input.startswith("http://") or url_input.startswith("https://"):
                webhook_url = url_input
            else:
                print("  URL должен начинаться с http:// или https://")
        except (EOFError, KeyboardInterrupt):
            print("\nПрервано.")
            sys.exit(0)

    return mode, webhook_url


def parse_args() -> argparse.Namespace:
    """
    Настраивает парсер аргументов командной строки.
    """
    parser = argparse.ArgumentParser(
        description="Интерактивный тест-раннер для slack-скрипта msgsender",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Примеры:\n"
            "  docker exec -it dev-msgsender python3 /opt/ispsystem/msgsender/test_slack.py\n"
            "  docker exec -it dev-msgsender python3 /opt/ispsystem/msgsender/test_slack.py --delivery\n"
            "  docker exec -it dev-msgsender python3 /opt/ispsystem/msgsender/test_slack.py --check-templates --webhook http://my-mattermost/hooks/...\n"
            "  docker exec -it dev-msgsender python3 /opt/ispsystem/msgsender/test_slack.py --all\n"
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--delivery",        action="store_true", help="Только Блок 1: доставка")
    group.add_argument("--check-templates", action="store_true", help="Только Блок 2: шаблоны")
    group.add_argument("--all",             action="store_true", help="Оба блока")
    
    parser.add_argument("--webhook", type=str, required=False, help="Обязательный Webhook URL для тестов (без моков)")
    
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    mode = "all"
    if args.delivery:
        mode = "delivery"
    elif args.check_templates:
        mode = "templates"

    webhook_url = args.webhook
    if not webhook_url:
        mode_from_menu, webhook_url = interactive_menu()
        if not args.delivery and not args.check_templates and not args.all:
            mode = mode_from_menu

    all_results: list[TestResult] = []

    if mode in ("delivery", "all"):
        all_results.extend(run_delivery_tests(webhook_url))

    if mode in ("templates", "all"):
        all_results.extend(run_template_tests(webhook_url=webhook_url))

    print_summary(all_results)

    if any(not r.passed for r in all_results):
        sys.exit(1)


if __name__ == "__main__":
    main()

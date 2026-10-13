import asyncio
import aiohttp
import json
import logging
import os
import sys
import sqlite3
from aiohttp import web

from log_setup import setup_logging
setup_logging()

DB_PATH = os.getenv('DB_PATH', 'data/chat.db')
MAX_API_URL = 'https://platform-api2.max.ru'

# TODO: pydci_notify — это внутренняя библиотека ISPsystem для уведомления ядра msgsender.
# Чтобы включить мгновенный сброс кэша для новых чатов MAX, необходимо:
# 1. Добавить `pydci_notify` в requirements.txt
# 2. Настроить внутренний PyPI в Dockerfile, чтобы pip мог ее скачать.
try:
    from pydci_notify import Notify
    HAS_PYDCI_NOTIFY = True
except ImportError:
    HAS_PYDCI_NOTIFY = False
    logging.warning("pydci_notify module not found. Core notifications will be stubbed.")

MAX_RETRIES = int(os.getenv('MAX_RETRIES', '5'))
PRODUCT = os.getenv('PRODUCT', 'vm')
PROXY_SERVICE_CONTAINER = os.getenv('PROXY_SERVICE', 'vm_box')
MSG_SENDER_URL = os.getenv('MSG_SENDER_URL', f'http://{PROXY_SERVICE_CONTAINER}/msgsender/v3')
MSGSENDER_BOT_CONF_RETRY_PERIOD = 10
MAX_LONG_POLLING_TIME = 30

sqlite_con = sqlite3.connect(DB_PATH)

def init_tables(connection):
    cursor = connection.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS maxsrv_chat ('
                   'chat_id INTEGER PRIMARY KEY, '
                   'username TEXT, '
                   'first_name TEXT, '
                   'last_name TEXT, '
                   'created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP'
                   ')')
    connection.commit()


def save_chat_to_db(chat_id, username='', first_name='', last_name=''):
    cursor = sqlite_con.cursor()
    cursor.execute(
        'INSERT OR IGNORE INTO maxsrv_chat (chat_id, username, first_name, last_name) '
        'VALUES (?, ?, ?, ?)',
        (chat_id, username, first_name, last_name)
    )
    sqlite_con.commit()
    logging.debug(f'save_chat_to_db: chat_id={chat_id}, username={username}')


def load_channels_from_db():
    return {
        r[0]: {'chat_id': r[0], 'username': r[1], 'first_name': r[2], 'last_name': r[3], 'created_at': r[4]}
        for r in sqlite_con.execute('SELECT chat_id, username, first_name, last_name, created_at FROM maxsrv_chat')
    }


async def check_max_token(session, token):
    """Check if the MAX bot token is valid via GET /me"""
    headers = {'Authorization': token}
    logging.debug(f"check_max_token: Validating token via GET {MAX_API_URL}/me")
    try:
        async with session.get(f'{MAX_API_URL}/me', headers=headers, timeout=10) as resp:
            if resp.status == 200:
                data = await resp.json()
                logging.info(f"check_max_token: Token is VALID. Bot username: {data.get('username')}")
                return True, data
            err_text = await resp.text()
            logging.error(f"check_max_token: Token is INVALID. Status: {resp.status}, Response: {err_text}")
            return False, err_text
    except Exception as e:
        logging.error(f"check_max_token: Network/SSL error: {e}")
        return False, str(e)


async def process_event_json(app, event_json):
    """Parse MAX API event, save new chats to DB, and notify msgsender."""
    update_type = event_json.get('update_type')
    payload = event_json.get('payload', {})
    
    logging.debug(f"process_event_json: Received event (type={update_type}): {json.dumps(event_json, ensure_ascii=False)}")
    
    chat_id = None
    sender = {}
    
    if update_type == 'bot_started':
        chat_id = event_json.get('chat_id')
        sender = event_json.get('user', {})
    elif update_type == 'message_created':
        message = event_json.get('message', {})
        recipient = message.get('recipient', {})
        chat_id = recipient.get('chat_id')
        sender = message.get('sender', {})
        
    if chat_id:
        known_channels = app.get('known_channels', {})
        
        if chat_id not in known_channels:
            save_chat_to_db(chat_id, sender.get('username', ''), sender.get('first_name', ''), sender.get('last_name', ''))
            
            app['known_channels'] = load_channels_from_db()
            logging.info(f"New chat discovered and saved: {chat_id} (name={sender.get('first_name', '')})")
            
            if HAS_PYDCI_NOTIFY:
                try:
                    notification = Notify()
                    notification.add_location(name="max_chat/0", notify_type=Notify.UPDATE_TYPE, additional_data={})
                    await notification.async_send()
                    logging.info("process_event_json: Sent pydci_notify notification for max_chat/0. Msgsender core should now see the new chat.")
                except Exception as e:
                    logging.error(f"process_event_json: Failed to send pydci_notify: {e}")
            else:
                logging.debug(f"New chat {chat_id} saved. pydci_notify is disabled. "
                              f"Uncomment the dependency in requirements.txt to notify msgsender core.")


async def fetch_token_from_msgsender(session):
    try:
        async with session.get(f'{MSG_SENDER_URL}/delivery_method/max/bot_config', headers={"internal-auth": "on"}, timeout=5) as resp:
            if resp.status == 200:
                return (await resp.json()).get('token')
            logging.debug(f'msgsender returned status {resp.status}')
    except Exception as e:
        logging.debug(f"Could not fetch token from msgsender: {e}")
    return None


def load_local_token():
    local_token = os.getenv('MAX_BOT_TOKEN')
    if not local_token and os.path.exists('data/token.txt'):
        try:
            with open('data/token.txt', 'r') as f:
                local_token = f.read().strip()
        except Exception as e:
            logging.error(f"Error reading data/token.txt: {e}")
    return local_token


async def bot_config_refresher(app):
    session = app['client_session']
    while True:
        token = await fetch_token_from_msgsender(session)
        if token:
            logging.debug("bot_config_refresher: Received token from msgsender. Validating...")
            is_valid, bot_info = await check_max_token(session, token)
            if is_valid:
                if token != app.get('max_token'):
                    logging.info(f"bot_config_refresher: Token updated from msgsender for bot: {bot_info.get('username')}")
                app['max_token'] = token
                app['bot_info'] = bot_info
                app['token_source'] = 'msgsender'
                app['token_ready_event'].set()
            else:
                logging.error(f"bot_config_refresher: Token from msgsender is invalid: {bot_info}")
        elif app['max_token'] is None:
            logging.debug("bot_config_refresher: Attempting to load local fallback token...")
            local_token = load_local_token()
            if local_token:
                is_valid, bot_info = await check_max_token(session, local_token)
                if is_valid:
                    app['max_token'] = local_token
                    app['bot_info'] = bot_info
                    app['token_source'] = 'local'
                    app['token_ready_event'].set()
                    logging.info(f"Fallback: loaded local token for bot: {bot_info.get('username')}")
                else:
                    logging.error("Local fallback token is invalid")

        await asyncio.sleep(MSGSENDER_BOT_CONF_RETRY_PERIOD)


async def bot_processor(app):
    """Long polling task to get updates from MAX API."""
    update_marker = None
    
    session = app['client_session']
    
    while True:
        if not app.get('max_token'):
            await app['token_ready_event'].wait()
            
        try:
            url = f"{MAX_API_URL}/updates?timeout={MAX_LONG_POLLING_TIME}"
            if update_marker:
                url += f"&marker={update_marker}"
            
            headers = {'Authorization': app['max_token']}
            logging.debug(f"bot_processor: Start Long Polling... GET {url}")
            
            async with session.get(url, headers=headers, timeout=MAX_LONG_POLLING_TIME + 5) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    events = data if isinstance(data, list) else data.get('updates', []) if isinstance(data, dict) else []
                    logging.debug(f"bot_processor: Long Polling returned HTTP 200. Events count: {len(events)}")
                    
                    for event in events:
                        event_id = event.get('update_id') or event.get('id')
                        if event_id is not None:
                            update_marker = event_id
                        await process_event_json(app, event)
                else:
                    err_text = await resp.text()
                    logging.error(f'bot_processor: GET /updates failed with status {resp.status}. Response: {err_text}')
                    await asyncio.sleep(5)
        except asyncio.TimeoutError:
            pass 
        except asyncio.CancelledError:
            break
        except Exception as e:
            logging.error(f'bot_processor error: {str(e)}')
            await asyncio.sleep(5)


async def on_startup(app):
    """Initialize shared resources: HTTP session."""
    app['client_session'] = aiohttp.ClientSession()
    app['bot_info'] = None
    app['max_token'] = None
    app['token_source'] = None
    app['token_ready_event'] = asyncio.Event()
    
    app['known_channels'] = load_channels_from_db()
    logging.info(f"max-srv started, loaded {len(app['known_channels'])} known channels from DB")
    
    app['refresher_task'] = asyncio.create_task(bot_config_refresher(app))
    app['bot_processor_task'] = asyncio.create_task(bot_processor(app))


async def on_cleanup(app):
    """Gracefully close shared resources."""
    app['refresher_task'].cancel()
    app['bot_processor_task'].cancel()
    await app['client_session'].close()
    sqlite_con.close()
    logging.info("max-srv stopped, aiohttp session and DB closed")


async def send_to_max_with_retry(app, chat_id, text, fmt="html", max_retries=MAX_RETRIES):
    token = app.get('max_token')
    if not token:
        return False, "Bot is not configured (no token)"
        
    try:
        clean_chat_id = int(str(chat_id).strip())
    except (ValueError, TypeError):
        clean_chat_id = str(chat_id).strip()

    url = f"{MAX_API_URL}/messages?chat_id={clean_chat_id}"
    headers = {'Authorization': token, 'Content-Type': 'application/json'}
    payload = {"text": text}
    if fmt:
        payload["format"] = fmt.lower() if isinstance(fmt, str) else fmt
    
    last_error = "Failed to send message"
    logging.info(f"send_to_max_with_retry: Starting send to chat_id={clean_chat_id}, max_retries={max_retries}")
    logging.debug(f"send_to_max_with_retry: Payload: {json.dumps(payload, ensure_ascii=False)}")
    
    for attempt in range(max_retries):
        try:
            async with app['client_session'].post(url, headers=headers, json=payload, timeout=10) as resp:
                if resp.status == 200:
                    logging.info(f"send_to_max_with_retry: Success on attempt {attempt+1}! Message delivered to {clean_chat_id}.")
                    return True, await resp.json()
                elif resp.status in (429, 500, 502, 503, 504):
                    sleep_time = 2 ** attempt
                    last_error = f"HTTP {resp.status}"
                    logging.warning(f"send_to_max_with_retry: {last_error}, sleeping {sleep_time}s (attempt {attempt+1}/{max_retries})")
                    await asyncio.sleep(sleep_time)
                    continue
                else:
                    err_text = await resp.text()
                    logging.error(f"send_to_max_with_retry: unrecoverable error {resp.status}: {err_text}")
                    return False, err_text
                    
        except asyncio.CancelledError:
            logging.warning("send_to_max_with_retry: task cancelled")
            return False, "Task cancelled"
        except aiohttp.ClientError as e:
            last_error = f"Network error: {e}"
            sleep_time = 2 ** attempt
            logging.warning(f"send_to_max_with_retry: {last_error}, sleeping {sleep_time}s")
            await asyncio.sleep(sleep_time)
        except Exception as e:
            logging.error(f"send_to_max_with_retry: unexpected error {e}")
            return False, str(e)
            
    return False, f"Max retries exceeded. Last error: {last_error}"


async def send_msg_handler(request):
    """POST /sendMsg — send a message to a MAX chat."""
    try:
        data = await request.json()
    except Exception:
        return web.json_response({'ok': False, 'error': 'Invalid JSON'}, status=400)
        
    chat_id, text = data.get('chat_id') or data.get('chat'), data.get('text') or data.get('msg')
    if not chat_id or not text:
        return web.json_response({'ok': False, 'error': 'Missing chat/msg parameters'}, status=400)
        
    fmt = data.get('format')
    if fmt is None:
        fmt = data.get('parse_mode', 'html')

    is_async = data.get('async')
    if isinstance(is_async, str):
        is_async = is_async.lower() in ('true', '1', 'yes', 'y', 't')
    else:
        is_async = bool(is_async)
    
    if is_async:
        asyncio.create_task(send_to_max_with_retry(request.app, chat_id, text, fmt=fmt))
        return web.json_response({'ok': True, 'status': 'accepted'}, status=202)
        
    success, result = await send_to_max_with_retry(request.app, chat_id, text, fmt=fmt)
    return web.json_response({'ok': success, 'result' if success else 'error': result}, status=200 if success else 400)


async def configure_bot_handler(request):
    """POST /configureBot — force-refresh bot token."""
    json_body = await request.json()
    token = json_body.get('token')
    if not token:
        raise web.HTTPBadRequest(reason="Missing 'token' in JSON body")
        
    is_valid, bot_info = await check_max_token(request.app['client_session'], token)
    if is_valid:
        request.app['max_token'] = token
        request.app['bot_info'] = bot_info
        request.app['token_ready_event'].set()
        logging.info(f"Token manually updated for bot: {bot_info.get('username')}")
        return web.json_response({'ok': True, 'msg': 'Token updated successfully'})
    else:
        return web.json_response({'ok': False, 'error': 'Invalid token', 'details': bot_info}, status=400)


async def get_bot_info_handler(request):
    """GET /getBotInfo — return current bot info."""
    if not request.app['max_token']:
        return web.json_response({'ok': False, 'error': 'Bot is not configured yet'}, status=404)
        
    return web.json_response({
        'ok': True, 
        'bot_info': request.app['bot_info'],
        'has_token': True
    })


async def get_known_channels_handler(request):
    """GET /getKnownChannels — return list of known chat_ids from DB."""
    channels = load_channels_from_db()
    return web.json_response(list(channels.values()))


async def webhook_handler(request):
    """POST /webhook — receive events from MAX platform (production mode)."""
    try:
        event_json = await request.json()
        await process_event_json(request.app, event_json)
        return web.json_response({'ok': True})
    except Exception as e:
        logging.error(f"webhook_handler error: {e}")
        return web.json_response({'ok': False, 'error': str(e)}, status=400)


def main():
    logging.info("max-srv starts")
    init_tables(sqlite_con)
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)

    app.add_routes([
        web.post('/sendMsg', send_msg_handler),
        web.post('/sendmsg', send_msg_handler),
        web.post('/configureBot', configure_bot_handler),
        web.get('/getBotInfo', get_bot_info_handler),
        web.get('/getKnownChannels', get_known_channels_handler),
        web.post('/webhook', webhook_handler),
    ])

    web.run_app(app, port=8080)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        logging.exception("Unexpected error occurred in main")
        sys.exit(1)

import asyncio
import aiohttp
import json
import logging
import os
import sys
from aiohttp import web

MAX_RETRIES = int(os.getenv('MAX_RETRIES', '5'))
LOG_FILE = os.getenv('LOG_FILE', '/var/log/slack-srv.log' if os.access('/var/log', os.W_OK) else 'slack-srv.log')

logging.basicConfig(level=logging.DEBUG,
                    filename=LOG_FILE,
                    format='%(asctime)s [pid=%(process)d] %(levelname)s %(message)s',
                    datefmt='%Y-%m-%d %H:%M:%S.%f')


async def on_startup(app):
    app['client_session'] = aiohttp.ClientSession()

async def on_cleanup(app):
    await app['client_session'].close()


async def send_to_slack_with_retry(webhook_url, text, session):
    payload = {"text": text}
    
    last_status = 500
    last_error_msg = "Failed to send message"

    for attempt in range(MAX_RETRIES + 1):
        try:
            logging.debug(f"Attempt {attempt + 1}/{MAX_RETRIES + 1} to send message to {webhook_url}")
            async with session.post(webhook_url, json=payload) as response:
                if response.status == 200:
                    logging.info(f"Successfully sent message to Slack on attempt {attempt + 1}")
                    return True, None, 200
                
                if response.status == 429:
                    retry_after = response.headers.get('Retry-After')
                    if retry_after and retry_after.isdigit():
                        sleep_time = int(retry_after)
                        logging.warning(f"Rate limited (429). Retry-After header found. Sleeping for {sleep_time} seconds.")
                    else:
                        sleep_time = 2 ** attempt
                        logging.warning(f"Rate limited (429) but no valid Retry-After header. Sleeping for {sleep_time} seconds (exponential backoff).")
                    last_status = 429
                    last_error_msg = "Rate limited (429)"
                elif response.status >= 500:
                    sleep_time = 2 ** attempt
                    logging.warning(f"Server error {response.status}. Sleeping for {sleep_time} seconds (exponential backoff).")
                    last_status = response.status
                    last_error_msg = f"Server error {response.status}"
                else:
                    error_text = await response.text()
                    logging.error(f"Client error {response.status}: {error_text}. Won't retry.")
                    return False, f"Client error {response.status}: {error_text}", response.status
        
        except aiohttp.ClientError as e:
            sleep_time = 2 ** attempt
            logging.warning(f"Network error: {e}. Sleeping for {sleep_time} seconds (exponential backoff).")
            last_status = 500
            last_error_msg = f"Network error: {str(e)}"
        except asyncio.CancelledError:
            logging.warning("Task cancelled")
            return False, "Task cancelled", 500
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
            return False, f"Unexpected error: {str(e)}", 500

        if attempt < MAX_RETRIES:
            await asyncio.sleep(sleep_time)
        else:
            logging.error(f"Failed to send message to Slack after {MAX_RETRIES + 1} attempts.")
            return False, last_error_msg, last_status


async def slack_send_msg_handler(request):
    try:
        json_body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({'ok': False, 'error': 'Invalid JSON'}, status=400)
        
    if 'webhook' not in json_body or 'text' not in json_body:
        return web.json_response({'ok': False, 'error': 'Missing webhook or text field'}, status=400)

    webhook = json_body['webhook']
    text = json_body['text']
    
    is_async = json_body.get('async')
    if isinstance(is_async, str):
        is_async = is_async.lower() in ('true', '1', 'yes', 'y', 't')
    else:
        is_async = bool(is_async)

    logging.debug(f'slack_send_msg_handler received request for webhook: {webhook}, async: {is_async}')

    session = request.app['client_session']
    
    if is_async:
        asyncio.create_task(send_to_slack_with_retry(webhook, text, session))
        return web.json_response({'ok': True, 'msg': 'Message accepted for delivery'}, status=202)
    else:
        success, error_msg, status_code = await send_to_slack_with_retry(webhook, text, session)
        if success:
            return web.json_response({'ok': True}, status=200)
        else:
            return web.json_response({'ok': False, 'error': error_msg}, status=status_code)


def main():
    logging.info("slack-srv starts")
    app = web.Application()
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    
    app.add_routes([
        web.post('/sendmsg', slack_send_msg_handler),
        web.post('/sendMsg', slack_send_msg_handler) 
    ])
    
    web.run_app(app, port=8080)

if __name__ == '__main__':
    try:
        main()
    except Exception:
        logging.exception("Unexpected error occurred in main")
        sys.exit(1)

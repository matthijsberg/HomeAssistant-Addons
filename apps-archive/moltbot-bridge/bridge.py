import asyncio
import logging
import os
import json
import signal
import sys
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ValidationError
import aiohttp
from aiohttp import web
import websockets

# --- Configuration & Validation ---

class AddonConfig(BaseModel):
    log_level: str = "info"
    ha_url: str = Field(..., description="URL to Home Assistant API")
    ha_token: str = Field(..., description="Long-lived Access Token")
    
    # Messaging
    bluebubbles_url: Optional[str] = None
    bluebubbles_token: Optional[str] = None
    whatsapp_provider: str = "twilio"
    whatsapp_sid: Optional[str] = None
    whatsapp_token: Optional[str] = None
    whatsapp_from: Optional[str] = None

# --- Logging Setup ---
def setup_logging(level_str: str):
    level = getattr(logging, level_str.upper(), logging.INFO)
    logging.basicConfig(
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        level=level,
        stream=sys.stdout
    )

logger = logging.getLogger("MoltbotAddon")

# --- Home Assistant Client ---
class HomeAssistantClient:
    def __init__(self, url: str, token: str):
        # Convert http(s) to ws(s)
        if url.startswith("http"):
            self.ws_url = url.replace("http", "ws") + "/websocket"
        else:
            self.ws_url = url
            
        self.token = token
        self.connection = None
        self._message_id = 1
        self._futures: Dict[int, asyncio.Future] = {}
        self._connected = False

    async def connect(self):
        logger.info(f"Connecting to Home Assistant at {self.ws_url}")
        try:
            extra_headers = {"Authorization": f"Bearer {self.token}"}
            # websockets 16.0+ uses additional_headers instead of extra_headers
            self.connection = await websockets.connect(self.ws_url, additional_headers=extra_headers)
            auth_msg = await self.connection.recv()
            auth_data = json.loads(auth_msg)
            
            if auth_data.get("type") == "auth_required":
                await self.connection.send(json.dumps({
                    "type": "auth",
                    "access_token": self.token
                }))
                
                auth_resp_raw = await self.connection.recv()
                auth_resp = json.loads(auth_resp_raw)
                
                if auth_resp.get("type") == "auth_ok":
                    logger.info("Authenticated with Home Assistant")
                    self._connected = True
                    # Start listening loop
                    asyncio.create_task(self.listen())
                else:
                    logger.error(f"Authentication failed: {auth_resp}")
                    raise ConnectionError(f"Auth failed: {auth_resp}")
            else:
                logger.warning(f"Unexpected initial sequence: {auth_data}")

        except Exception as e:
            logger.error(f"Failed to connect to HA: {e}")
            raise

    async def listen(self):
        try:
            async for message in self.connection:
                try:
                    data = json.loads(message)
                    # logger.debug(f"Received: {data}")
                    
                    # Handle responses to our requests
                    if "id" in data and data["id"] in self._futures:
                        self._futures[data["id"]].set_result(data)
                        del self._futures[data["id"]]
                        
                    # Event handling can go here
                except json.JSONDecodeError:
                    logger.warning(f"Received invalid JSON: {message}")
        except websockets.ConnectionClosed:
            logger.warning("Connection closed")
            self._connected = False
        except Exception as e:
            logger.error(f"Listen loop error: {e}")
            self._connected = False
            
    async def call_service(self, domain: str, service: str, service_data: Dict[str, Any] = None):
        if not self._connected:
             logger.warning("Cannot call service, not connected to HA")
             return None

        msg_id, future = self._create_future()
        msg = {
            "id": msg_id,
            "type": "call_service",
            "domain": domain,
            "service": service,
            "service_data": service_data or {}
        }
        await self.connection.send(json.dumps(msg))
        return await future

    async def get_states(self):
        if not self._connected:
             logger.warning("Cannot get states, not connected to HA")
             return None

        msg_id, future = self._create_future()
        msg = {
            "id": msg_id,
            "type": "get_states"
        }
        await self.connection.send(json.dumps(msg))
        return await future

    def _create_future(self):
        self._message_id += 1
        future = asyncio.Future()
        self._futures[self._message_id] = future
        return self._message_id, future
        
    async def close(self):
        if self.connection:
            await self.connection.close()

    async def update_addon_options(self, addon: str, options: Dict[str, Any]):
        return await self.call_service("hassio", "addon_update", {
            "addon": addon,
            "options": options
        })

# --- Integration Stubs ---
class GoogleIntegration:
    def __init__(self, services_json_path: str):
        self.creds_path = services_json_path
        
    async def sync_devices(self):
        logger.info("Syncing devices with Google Cloud...")
        # TODO: Implement Google HomeGraph API calls
        pass

class MessagingBridge:
    def __init__(self, provider: str, config: AddonConfig):
        self.provider = provider
        self.config = config
        
    async def send_message(self, target: str, message: str):
        logger.info(f"Sending message via {self.provider} to {target}: {message}")
        # TODO: Implement Twilio/Matrix/BlueBubbles logic
        pass

# --- Application ---
async def main():
    # 1. Load Config
    try:
        config_data = {
            "log_level": os.getenv("LOG_LEVEL", "info"),
            "ha_url": os.getenv("HA_URL"),
            "ha_token": os.getenv("HA_TOKEN"),
            "bluebubbles_url": os.getenv("BLUEBUBBLES_URL"),
            "bluebubbles_token": os.getenv("BLUEBUBBLES_TOKEN"),
            "whatsapp_provider": os.getenv("WHATSAPP_PROVIDER"),
            "whatsapp_sid": os.getenv("WHATSAPP_SID"),
            "whatsapp_token": os.getenv("WHATSAPP_TOKEN"),
            "whatsapp_from": os.getenv("WHATSAPP_FROM"),
        }
        # Filter purely None/missing values so defaults work if not in env
        config_data = {k: v for k, v in config_data.items() if v is not None}
        
        config = AddonConfig(**config_data)
    except ValidationError as e:
        print(f"Configuration Error:\n{e}")
        # In Add-ons, better to exit so supervisor restarts or logs error clearly
        sys.exit(1)

    setup_logging(config.log_level)
    logger.info("Starting Moltbot-HA Bridge Add-on...")

    # 2. Initialize Clients
    ha_client = HomeAssistantClient(config.ha_url, config.ha_token)
    
    # 3. Setup Web Server for Chat
    app = web.Application()
    
    async def handle_chat(request):
        try:
            data = await request.json()
            user_message = data.get('message', '')
            logger.info(f"Chat received: {user_message}")
            
            # Forward message to Moltbot Gateway
            moltbot_url = "http://localhost:18789/api/chat"
            async with aiohttp.ClientSession() as session:
                try:
                    payload = {
                        "messages": [{"role": "user", "content": user_message}],
                        "stream": False 
                    }
                    async with session.post(moltbot_url, json=payload) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            bot_text = data.get("content") or data.get("message") or str(data)
                            return web.json_response({'response': bot_text})
                        else:
                            err_text = await resp.text()
                            logger.error(f"Moltbot API error {resp.status}: {err_text}")
                            return web.json_response({'response': f"Error from Moltbot: {resp.status}"})
                except Exception as ex:
                    logger.error(f"Failed to contact Moltbot: {ex}")
                    return web.json_response({'response': "Moltbot is not reachable yet (still starting?)."})
                    
        except Exception as e:
            logger.error(f"Chat error: {e}")
            return web.json_response({'error': str(e)}, status=500)

    async def handle_available_models(request):
        try:
            logger.info("Fetching available models from Ollama...")
            async with aiohttp.ClientSession() as session:
                async with session.get("https://ollama.com/library") as resp:
                    if resp.status != 200:
                        return web.json_response({"error": "Failed to fetch library"}, status=500)
                    html = await resp.text()
                    
                    # Simple regex to find /library/model-name
                    import re
                    pattern = r'href="/library/([^/"]+)"'
                    models = re.findall(pattern, html)
                    unique_models = sorted(list(set(models)))
                    
                    # Add variants for popular ones
                    rich_models = []
                    special_cases = {
                        "gemma3": ["270m", "1b", "4b", "12b", "27b", "vision"],
                        "llama3.2": ["1b", "3b"],
                        "llama3.1": ["8b", "70b"],
                        "phi3.5": ["latest"],
                        "gemma2": ["2b", "9b", "27b"],
                        "mistral": ["7b"]
                    }
                    
                    for m in unique_models:
                        variants = special_cases.get(m, ["latest"])
                        rich_models.append({"name": m, "variants": variants})
                        
                    return web.json_response({"models": rich_models})
        except Exception as e:
            logger.error(f"Error fetching models: {e}")
            return web.json_response({"error": str(e)}, status=500)

    async def handle_select_model(request):
        try:
            data = await request.json()
            model = data.get("model")
            if not model:
                return web.json_response({"error": "No model specified"}, status=400)
            
            logger.info(f"Setting Ollama model to: {model}")
            # Update the Ollama add-on options via Home Assistant
            resp = await ha_client.update_addon_options("ollama", {"model": model})
            
            if resp and resp.get("success"):
                return web.json_response({"status": "ok", "message": f"Model set to {model}. Ollama will restart/pull the model."})
            else:
                return web.json_response({"error": "Failed to update add-on options", "details": resp}, status=500)
                
        except Exception as e:
            logger.error(f"Error selecting model: {e}")
            return web.json_response({"error": str(e)}, status=500)

    app.router.add_post('/api/chat', handle_chat)
    app.router.add_get('/api/models/available', handle_available_models)
    app.router.add_post('/api/models/select', handle_select_model)
    
    # Serve index.html explicitly to ensure Ingress finds it at root
    async def index(request):
        web_dir = os.path.join(os.path.dirname(__file__), 'web')
        index_file = os.path.join(web_dir, 'index.html')
        if os.path.exists(index_file):
            return web.FileResponse(index_file)
        return web.Response(text="Web interface not found", status=404)

    app.router.add_get('/', index)
    app.router.add_get('/index.html', index)
    
    # Optional: Serve other static files if needed, avoiding root conflict
    # web_dir = os.path.join(os.path.dirname(__file__), 'web')
    # app.router.add_static('/static/', web_dir)

    # Create web runner
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', 8099)
    await site.start()
    logger.info("Web interface started on port 8099")

    # 4. Main Loop & Signal Handling
    stop_event = asyncio.Event()
    
    def signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()
        
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(signal.SIGTERM, signal_handler)
    loop.add_signal_handler(signal.SIGINT, signal_handler)

    logger.info("Bridge is starting loop. Connecting to HA...")
    
    while not stop_event.is_set():
        try:
            if not ha_client._connected:
                try:
                    await ha_client.connect()
                except (ConnectionError, OSError) as e:
                    logger.error(f"Connection failed: {e}. Retrying in 10s...")
                    try:
                        await asyncio.wait_for(stop_event.wait(), timeout=10)
                    except asyncio.TimeoutError:
                        continue 
                    
            # If connected, just wait (the listen task runs in background)
            # We check periodically if we are still connected or if stop signal came
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass # Just a loop cycle to check status
                
        except Exception as e:
            logger.error(f"Unexpected error in main loop: {e}", exc_info=True)
            await asyncio.sleep(5)

    logger.info("Shutting down...")
    await site.stop()
    await runner.cleanup()
    await ha_client.close()
    logger.info("Goodbye.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

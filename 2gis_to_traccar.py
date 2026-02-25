#!/usr/bin/env python3
"""
2GIS to Traccar Bridge
Connects to 2GIS WebSocket, receives location data, and forwards it to Traccar API
"""

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from config import (
    TWOGIS_WS_URL, TRACCAR_BASE_URL, LOG_LEVEL, LOG_FILE,
    WEBHOOK_URL, WEBHOOK_TOKEN, WEBHOOK_TABLE_NAME,
    TWOGIS_REFRESH_TOKEN, TWOGIS_AUTH_REFRESH_URL, TWOGIS_TOKEN_FILE,
)

# Setup logging
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper()),
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TraccarClient:
    """Client for sending data to Traccar using OsmAnd protocol"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip('/')
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    def _map_movement_to_activity(self, movement_status: Optional[str], is_moving: Optional[bool]) -> str:
        """Map 2GIS movement status to OsmAnd activity type"""
        if movement_status == "stopped":
            return "still"
        elif movement_status == "moving":
            return "in_vehicle"
        elif is_moving is not None:
            return "in_vehicle" if is_moving else "still"
        else:
            return "still"  # Default
    
    async def send_position(self, device_id: str, lat: float, lon: float, 
                          speed: Optional[float] = None, course: Optional[float] = None,
                          accuracy: Optional[float] = None, battery: Optional[float] = None,
                          is_charging: Optional[bool] = None, is_moving: Optional[bool] = None,
                          movement_status: Optional[str] = None, extras: Optional[Dict[str, Any]] = None) -> bool:
        """Send position data to Traccar using OsmAnd POST protocol with JSON format"""
        if not self.session:
            logger.error("Traccar session not initialized")
            return False
        
        # Convert speed from km/h to m/s (OsmAnd JSON format uses m/s)
        speed_ms = None
        if speed is not None:
            speed_ms = speed / 3.6  # km/h to m/s conversion
        
        # Prepare OsmAnd JSON format payload
        payload = {
            "location": {
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "coords": {
                    "latitude": lat,
                    "longitude": lon,
                    "accuracy": accuracy if accuracy is not None else 0,
                    "speed": speed_ms if speed_ms is not None else 0,
                    "heading": course if course is not None else 0,
                    "altitude": 0  # 2GIS doesn't provide altitude
                },
                "is_moving": is_moving if is_moving is not None else False,
                "odometer": 0,  # 2GIS doesn't provide odometer
                "event": "motionchange",  # Default event type
                "battery": {
                    "level": battery if battery is not None else 1,
                    "is_charging": is_charging if is_charging is not None else False
                },
                "activity": {
                    "type": self._map_movement_to_activity(movement_status, is_moving)
                },
                "extras": extras if extras is not None else {}
            },
            "device_id": device_id,
            "test": "Hello world"
        }
        
        try:
            # Log the request for debugging
            logger.debug(f"Sending OsmAnd POST request to: {self.base_url}")
            logger.debug(f"Payload: {json.dumps(payload, indent=2)}")
            
            # Send POST request to Traccar OsmAnd endpoint
            async with self.session.post(
                self.base_url,
                json=payload,
                headers={"Content-Type": "application/json"}
            ) as response:
                response_text = await response.text()
                if response.status == 200:
                    logger.info(f"Position sent successfully: {lat}, {lon}")
                    return True
                else:
                    logger.error(f"Failed to send position: {response.status} - {response_text}")
                    logger.error(f"Request URL: {response.url}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error sending position to Traccar: {e}")
            return False


class WebhookClient:
    """Client for sending data to n8n webhook endpoint"""
    
    def __init__(self, webhook_url: str, webhook_token: str, table_name: str):
        self.webhook_url = webhook_url
        self.webhook_token = webhook_token
        self.table_name = table_name
        self.session = None
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def send_data(self, data: Dict[str, Any]) -> bool:
        """Send data to webhook endpoint"""
        if not self.session:
            logger.error("Webhook session not initialized")
            return False
        
        if not self.webhook_url or not self.webhook_token:
            logger.debug("Webhook not configured, skipping webhook send")
            return True
        
        # Prepare the payload - send raw 2GIS data directly
        payload = {
            "tableName": self.table_name,
            "data": data
        }
        
        headers = {
            "Authorization": f"Bearer {self.webhook_token}",
            "Content-Type": "application/json"
        }
        
        try:
            logger.debug(f"Sending webhook data to: {self.webhook_url}")
            logger.debug(f"Payload: {payload}")
            
            async with self.session.post(
                self.webhook_url,
                json=payload,
                headers=headers
            ) as response:
                response_text = await response.text()
                if response.status == 200:
                    logger.info(f"Data sent to webhook successfully")
                    return True
                else:
                    logger.error(f"Failed to send data to webhook: {response.status} - {response_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Error sending data to webhook: {e}")
            return False


def _parse_set_cookie_header(header: str) -> Dict[str, str]:
    """Parse Set-Cookie header: name=value; Max-Age=...; Path=... etc."""
    cookies = {}
    main_part = header.split(";")[0].strip()
    if "=" in main_part:
        name, _, value = main_part.partition("=")
        name = name.strip()
        if name:
            cookies[name] = value.strip()
    return cookies


def _parse_set_cookie_expiry(header: str) -> Optional[int]:
    """Parse Max-Age or Expires from Set-Cookie. Returns seconds until expiry, or None."""
    from email.utils import parsedate_to_datetime
    parts = [p.strip() for p in header.split(";")[1:]]
    max_age = None
    expires = None
    for part in parts:
        if part.lower().startswith("max-age="):
            try:
                max_age = int(part.split("=", 1)[1].strip())
            except (ValueError, IndexError):
                pass
        elif part.lower().startswith("expires="):
            try:
                date_str = part.split("=", 1)[1].strip()
                expires = parsedate_to_datetime(date_str)
            except (ValueError, IndexError, TypeError):
                pass
    if max_age is not None:
        return max_age
    if expires is not None:
        now = datetime.now(expires.tzinfo) if expires.tzinfo else datetime.now(timezone.utc)
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        return max(0, int((expires - now).total_seconds()))
    return None


def _parse_all_set_cookies(response_headers) -> Dict[str, str]:
    """Parse all Set-Cookie headers from response."""
    cookies = {}
    for value in response_headers.getall("Set-Cookie", []):
        parsed = _parse_set_cookie_header(value)
        cookies.update(parsed)
    return cookies


def _parse_expiry_from_set_cookies(response_headers) -> Optional[int]:
    """Get expiry seconds from Set-Cookie headers (Max-Age or Expires)."""
    for value in response_headers.getall("Set-Cookie", []):
        expiry = _parse_set_cookie_expiry(value)
        if expiry is not None:
            return expiry
    return None


def _build_ws_url_with_token(base_ws_url: str, token: str) -> str:
    """Replace token in WebSocket URL."""
    parsed = urlparse(base_ws_url)
    params = parse_qs(parsed.query)
    params["token"] = [token]
    new_query = urlencode(params, doseq=True)
    return urlunparse(parsed._replace(query=new_query))


class TwoGisAuthClient:
    """Client for refreshing 2GIS auth tokens via /_/auth/refresh. Access token comes from refresh response."""

    def __init__(
        self,
        refresh_url: str,
        refresh_token: str,
        token_file: Optional[str] = None,
    ):
        self.refresh_url = refresh_url
        self.refresh_token = refresh_token
        self.access_token: Optional[str] = None  # Obtained from refresh response
        self.token_file = Path(token_file) if token_file else None
        self.session: Optional[aiohttp.ClientSession] = None
        self._next_refresh_in_seconds: Optional[int] = 3000  # Default 50 min if no expiry in response

    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()

    def _load_tokens_from_file(self) -> bool:
        """Load refresh_token, access_token from file. Returns True if loaded."""
        if not self.token_file or not self.token_file.exists():
            return False
        try:
            data = json.loads(self.token_file.read_text())
            r = data.get("dg5_auth_refresh_token")
            a = data.get("dg5_auth_access_token")
            if r:
                self.refresh_token = r
                if a:
                    self.access_token = a
                return True
        except Exception as e:
            logger.debug(f"Could not load tokens from file: {e}")
        return False

    def _save_tokens_to_file(self, refresh_token: str, access_token: Optional[str]):
        """Persist tokens to file."""
        if not self.token_file or not access_token:
            return
        try:
            self.token_file.parent.mkdir(parents=True, exist_ok=True)
            self.token_file.write_text(
                json.dumps(
                    {
                        "dg5_auth_refresh_token": refresh_token,
                        "dg5_auth_access_token": access_token,
                    },
                    indent=2,
                )
            )
            logger.debug(f"Saved refreshed tokens to {self.token_file}")
        except Exception as e:
            logger.warning(f"Could not save tokens to file: {e}")

    def get_next_refresh_seconds(self) -> int:
        """Seconds to wait before next refresh (from cookie expiry, or default 50 min)."""
        return self._next_refresh_in_seconds or 3000

    async def refresh(self) -> Optional[str]:
        """
        Call 2GIS auth refresh endpoint. Returns new access token or None.
        Access token comes from refresh response. Updates _next_refresh_in_seconds from cookie expiry.
        """
        if not self.session:
            logger.error("Auth session not initialized")
            return None

        cookies: Dict[str, str] = {"dg5_auth_refresh_token": self.refresh_token}
        if self.access_token:
            cookies["dg5_auth_access_token"] = self.access_token

        headers = {
            "accept": "application/json, text/plain, */*",
            "accept-language": "en-US,en;q=0.9,ru;q=0.8",
            "cache-control": "no-cache",
            "content-length": "0",
            "origin": "https://2gis.kz",
            "referer": "https://2gis.kz/almaty",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36",
        }

        try:
            async with self.session.post(
                self.refresh_url,
                headers=headers,
                cookies=cookies,
            ) as resp:
                new_cookies = _parse_all_set_cookies(resp.headers)
                new_refresh = new_cookies.get("dg5_auth_refresh_token")
                new_access = new_cookies.get("dg5_auth_access_token")

                if new_refresh:
                    self.refresh_token = new_refresh
                    logger.info("Refreshed dg5_auth_refresh_token")
                if new_access:
                    self.access_token = new_access
                    logger.info("Refreshed dg5_auth_access_token")

                if new_refresh or new_access:
                    self._save_tokens_to_file(self.refresh_token, self.access_token)

                # Parse expiry from Set-Cookie; refresh at 80% of lifetime
                expiry = _parse_expiry_from_set_cookies(resp.headers)
                if expiry is not None:
                    self._next_refresh_in_seconds = int(expiry * 0.8)
                    logger.debug(f"Next refresh in {self._next_refresh_in_seconds}s (from cookie expiry)")

                return self.access_token
        except Exception as e:
            logger.error(f"Auth refresh failed: {e}")
            return None


class TwoGISWebSocketClient:
    """Client for connecting to 2GIS WebSocket"""

    def __init__(
        self,
        ws_url: str,
        traccar_client: TraccarClient,
        webhook_client: Optional[WebhookClient] = None,
        auth_client: Optional[TwoGisAuthClient] = None,
    ):
        self.base_ws_url = ws_url
        self.traccar_client = traccar_client
        self.webhook_client = webhook_client
        self.auth_client = auth_client
        self.websocket = None
        self.running = False

    def _get_ws_url(self) -> Optional[str]:
        """Get WebSocket URL. When using auth, injects access token from refresh. Returns None if no token."""
        if self.auth_client:
            if not self.auth_client.access_token:
                return None
            return _build_ws_url_with_token(self.base_ws_url, self.auth_client.access_token)
        return self.base_ws_url

    async def connect(self):
        """Connect to 2GIS WebSocket. When using refresh, gets access token and injects into URL before connecting."""
        if self.auth_client:
            new_token = await self.auth_client.refresh()
            if not new_token:
                logger.error("Token refresh failed, cannot connect")
                return False
            logger.info("Token refreshed before connect")
        ws_url = self._get_ws_url()
        if not ws_url:
            logger.error("No WebSocket URL (missing access token)")
            return False
        try:
            self.websocket = await websockets.connect(ws_url)
            logger.info("Connected to 2GIS WebSocket")
            return True
        except Exception as e:
            logger.error(f"Failed to connect to 2GIS WebSocket: {e}")
            return False
    
    async def handle_message(self, message: str):
        """Handle incoming WebSocket message"""
        try:
            data = json.loads(message)
            
            # Check if this is a friendState message with location data
            if data.get("type") == "friendState":
                payload = data.get("payload", {})
                friend_id = payload.get("id")
                location = payload.get("location")
                battery = payload.get("battery", {})
                movement = payload.get("movement", {})

                # Send raw data to webhook immediately after parsing
                if self.webhook_client:
                    webhook_success = await self.webhook_client.send_data(data)
                    if not webhook_success:
                        logger.warning("Failed to send data to webhook")
                
                if friend_id and location and isinstance(location, dict) and "lat" in location and "lon" in location:
                    # Use 2GIS friend ID as device ID
                    device_id = f"{friend_id}"
                    
                    lat = location["lat"]
                    lon = location["lon"]
                    speed = location.get("speed")
                    course = location.get("azimuth")
                    accuracy = location.get("accuracy")
                    battery_level = battery.get("level")
                    is_charging = battery.get("isCharging")
                    
                    # Extract movement status - "stopped" means not moving, anything else means moving
                    movement_status = movement.get("status")
                    is_moving = movement_status != "stopped" if movement_status is not None else None
                    
                    # Convert speed from m/s to km/h if provided
                    if speed is not None:
                        speed = speed * 3.6
                    
                    # Prepare extras with additional 2GIS data not present in main structure
                    extras = {}
                    
                    # Add additional 2GIS payload fields not in main structure
                    if "lastSeen" in payload and payload["lastSeen"] is not None:
                        # Convert timestamp to ISO format
                        last_seen_dt = datetime.fromtimestamp(payload["lastSeen"] / 1000)
                        extras["2gis_lastSeen"] = last_seen_dt.isoformat() + "Z"
                    
                    if "locationPlace" in payload:
                        location_place = payload["locationPlace"]
                        # Add flattened location place data
                        if location_place and isinstance(location_place, dict):
                            if "object" in location_place and location_place["object"] and isinstance(location_place["object"], dict) and "id" in location_place["object"]:
                                location_id = location_place["object"]["id"]
                                extras["2gis_locationId"] = location_id
                                extras["2gis_locationUrl"] = f"https://2gis.kz/almaty/firm/{location_id}"
                            if "object" in location_place and location_place["object"] and isinstance(location_place["object"], dict) and "regionId" in location_place["object"]:
                                extras["2gis_regionId"] = location_place["object"]["regionId"]
                            if "status" in location_place:
                                extras["2gis_locationStatus"] = location_place["status"]
                    
                    # Add stoppedAt timestamp if available in movement data
                    if movement and "stoppedAt" in movement and movement["stoppedAt"] is not None:
                        stopped_at_dt = datetime.fromtimestamp(movement["stoppedAt"] / 1000)
                        extras["2gis_stoppedAt"] = stopped_at_dt.isoformat() + "Z"
                    
                    # Send to Traccar
                    success = await self.traccar_client.send_position(
                        device_id=device_id,
                        lat=lat,
                        lon=lon,
                        speed=speed,
                        course=course,
                        accuracy=accuracy,
                        battery=battery_level,
                        is_charging=is_charging,
                        is_moving=is_moving,
                        movement_status=movement_status,
                        extras=extras
                    )
                    
                    if success:
                        charging_status = "charging" if is_charging else "not charging" if is_charging is not None else "unknown"
                        movement_status = "moving" if is_moving else "stopped" if is_moving is not None else "unknown"
                        speed_info = f"speed: {speed:.1f} km/h" if speed is not None else "speed: unknown"
                        logger.info(f"Processed location for {device_id}: {lat}, {lon} (battery: {battery_level}, {charging_status}, {movement_status}, {speed_info})")
                    else:
                        logger.warning(f"Failed to send location for {device_id}: {lat}, {lon}")
                else:
                    logger.debug("Message received but no valid location data or friend ID")
            else:
                logger.debug(f"Received message type: {data.get('type', 'unknown')}")
                
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON message: {e}")
        except Exception as e:
            logger.error(f"Error handling message: {e}")
    
    async def _periodic_refresh_task(self):
        """Background task to refresh token periodically (interval from cookie Max-Age/Expires)."""
        while self.running and self.auth_client:
            delay = self.auth_client.get_next_refresh_seconds()
            logger.debug(f"Next token refresh in {delay}s")
            await asyncio.sleep(delay)
            if not self.running:
                break
            new_token = await self.auth_client.refresh()
            if new_token:
                logger.info("Periodic token refresh completed")

    async def run(self):
        """Main run loop"""
        if not await self.connect():
            return

        self.running = True
        logger.info("Starting 2GIS to Traccar bridge...")

        refresh_task = None
        if self.auth_client:
            refresh_task = asyncio.create_task(self._periodic_refresh_task())

        try:
            async for message in self.websocket:
                if not self.running:
                    break
                await self.handle_message(message)

        except ConnectionClosed:
            logger.warning("WebSocket connection closed")
        except WebSocketException as e:
            logger.error(f"WebSocket error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
        finally:
            self.running = False
            if refresh_task:
                refresh_task.cancel()
                try:
                    await refresh_task
                except asyncio.CancelledError:
                    pass
            await self.disconnect()
    
    async def disconnect(self):
        """Disconnect from WebSocket"""
        self.running = False
        if self.websocket:
            await self.websocket.close()
            logger.info("Disconnected from 2GIS WebSocket")


@asynccontextmanager
async def _optional_auth_context(auth_client: Optional[TwoGisAuthClient]):
    """Async context manager that enters auth_client if present, else yields None."""
    if auth_client is None:
        yield None
    else:
        async with auth_client:
            yield auth_client


def _create_auth_client() -> Optional[TwoGisAuthClient]:
    """Create TwoGisAuthClient if refresh token is configured. Access token comes from refresh only."""
    if not TWOGIS_REFRESH_TOKEN:
        return None
    auth = TwoGisAuthClient(
        refresh_url=TWOGIS_AUTH_REFRESH_URL,
        refresh_token=TWOGIS_REFRESH_TOKEN,
        token_file=TWOGIS_TOKEN_FILE,
    )
    auth._load_tokens_from_file()
    if auth.access_token:
        logger.info("Loaded tokens from file")
    return auth


async def main():
    """Main function"""
    logger.info("Starting 2GIS to Traccar bridge...")

    # Initialize webhook client if configured
    webhook_client = None
    if WEBHOOK_URL and WEBHOOK_TOKEN:
        webhook_client = WebhookClient(WEBHOOK_URL, WEBHOOK_TOKEN, WEBHOOK_TABLE_NAME)
        logger.info(f"Webhook configured: {WEBHOOK_URL} (table: {WEBHOOK_TABLE_NAME})")
    else:
        logger.info("Webhook not configured, skipping webhook functionality")

    auth_client = _create_auth_client()
    if auth_client:
        logger.info("Token refresh enabled")

    # Initialize Traccar client (no authentication needed with OsmAnd protocol)
    async with TraccarClient(TRACCAR_BASE_URL) as traccar_client:
        async with _optional_auth_context(auth_client) as auth:
            if webhook_client:
                async with webhook_client:
                    client = TwoGISWebSocketClient(TWOGIS_WS_URL, traccar_client, webhook_client, auth)
                    while True:
                        try:
                            await client.run()
                        except Exception as e:
                            logger.error(f"Error in main loop: {e}")
                            logger.info("Retrying in 30 seconds...")
                            await asyncio.sleep(30)
            else:
                client = TwoGISWebSocketClient(TWOGIS_WS_URL, traccar_client, auth_client=auth)
                while True:
                    try:
                        await client.run()
                    except Exception as e:
                        logger.error(f"Error in main loop: {e}")
                        logger.info("Retrying in 30 seconds...")
                        await asyncio.sleep(30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}")

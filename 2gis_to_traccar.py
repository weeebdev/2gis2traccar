#!/usr/bin/env python3
"""
2GIS to Traccar Bridge
Connects to 2GIS WebSocket, receives location data, and forwards it to Traccar API
"""

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Dict, Any, Optional

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from config import (
    TWOGIS_WS_URL, TRACCAR_BASE_URL, LOG_LEVEL, LOG_FILE,
    WEBHOOK_URL, WEBHOOK_TOKEN, WEBHOOK_TABLE_NAME
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
            "device_id": device_id
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


class TwoGISWebSocketClient:
    """Client for connecting to 2GIS WebSocket"""
    
    def __init__(self, ws_url: str, traccar_client: TraccarClient, webhook_client: Optional[WebhookClient] = None):
        self.ws_url = ws_url
        self.traccar_client = traccar_client
        self.webhook_client = webhook_client
        self.websocket = None
        self.running = False
    
    async def connect(self):
        """Connect to 2GIS WebSocket"""
        try:
            self.websocket = await websockets.connect(self.ws_url)
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
    
    async def run(self):
        """Main run loop"""
        if not await self.connect():
            return
        
        self.running = True
        logger.info("Starting 2GIS to Traccar bridge...")
        
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
            await self.disconnect()
    
    async def disconnect(self):
        """Disconnect from WebSocket"""
        self.running = False
        if self.websocket:
            await self.websocket.close()
            logger.info("Disconnected from 2GIS WebSocket")


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
    
    # Initialize Traccar client (no authentication needed with OsmAnd protocol)
    async with TraccarClient(TRACCAR_BASE_URL) as traccar_client:
        # Initialize webhook client if configured
        if webhook_client:
            async with webhook_client:
                # Initialize and run 2GIS WebSocket client
                client = TwoGISWebSocketClient(TWOGIS_WS_URL, traccar_client, webhook_client)
                
                # Keep trying to connect and run
                while True:
                    try:
                        await client.run()
                    except Exception as e:
                        logger.error(f"Error in main loop: {e}")
                        logger.info("Retrying in 30 seconds...")
                        await asyncio.sleep(30)
        else:
            # Initialize and run 2GIS WebSocket client without webhook
            client = TwoGISWebSocketClient(TWOGIS_WS_URL, traccar_client)
            
            # Keep trying to connect and run
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

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
    TWOGIS_WS_URL, TRACCAR_BASE_URL, LOG_LEVEL, LOG_FILE
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
    
    async def send_position(self, device_id: str, lat: float, lon: float, 
                          speed: Optional[float] = None, course: Optional[float] = None,
                          accuracy: Optional[float] = None, battery: Optional[float] = None) -> bool:
        """Send position data to Traccar using OsmAnd protocol"""
        if not self.session:
            logger.error("Traccar session not initialized")
            return False
        
        # Convert speed from km/h to knots (OsmAnd protocol uses knots by default)
        speed_knots = None
        if speed is not None:
            speed_knots = speed * 0.539957  # km/h to knots conversion
        
        # Prepare OsmAnd protocol parameters
        params = {
            'id': device_id,
            'lat': lat,
            'lon': lon,
            'timestamp': int(datetime.utcnow().timestamp()),
            'valid': 'true'  # Mark location as valid
        }
        
        # Add optional parameters only if they have meaningful values
        if speed_knots is not None and speed_knots > 0:
            params['speed'] = speed_knots
            
        if course is not None:
            params['bearing'] = course
            
        if accuracy is not None and accuracy > 0:
            params['accuracy'] = accuracy
            
        # Add battery level if available (OsmAnd uses 'batt' parameter)
        if battery is not None:
            params['batt'] = int(battery * 100)  # Convert to percentage
        
        try:
            # Log the request for debugging
            logger.debug(f"Sending OsmAnd request to: {self.base_url}")
            logger.debug(f"Parameters: {params}")
            
            # Send GET request to Traccar OsmAnd endpoint (no /api/osmand needed)
            async with self.session.get(
                self.base_url,
                params=params
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


class TwoGISWebSocketClient:
    """Client for connecting to 2GIS WebSocket"""
    
    def __init__(self, ws_url: str, traccar_client: TraccarClient):
        self.ws_url = ws_url
        self.traccar_client = traccar_client
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
                
                if friend_id and location and "lat" in location and "lon" in location:
                    # Use 2GIS friend ID as device ID with prefix
                    device_id = f"{friend_id}"
                    
                    lat = location["lat"]
                    lon = location["lon"]
                    speed = location.get("speed")
                    course = location.get("azimuth")
                    accuracy = location.get("accuracy")
                    battery_level = battery.get("level")
                    
                    # Convert speed from m/s to km/h if provided
                    if speed is not None:
                        speed = speed * 3.6
                    
                    # Send to Traccar
                    success = await self.traccar_client.send_position(
                        device_id=device_id,
                        lat=lat,
                        lon=lon,
                        speed=speed,
                        course=course,
                        accuracy=accuracy,
                        battery=battery_level
                    )
                    
                    if success:
                        logger.info(f"Processed location for {device_id}: {lat}, {lon} (battery: {battery_level})")
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
    
    # Initialize Traccar client (no authentication needed with OsmAnd protocol)
    async with TraccarClient(TRACCAR_BASE_URL) as traccar_client:
        # Initialize and run 2GIS WebSocket client
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

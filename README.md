# 2GIS to Traccar Bridge

This Python script connects to the 2GIS WebSocket API, receives real-time location data, and forwards it to a Traccar GPS tracking server using the **OsmAnd protocol** (no authentication required!).

## Features

- Connects to 2GIS WebSocket API for real-time location updates
- Parses friendState messages with location, battery, and movement data
- Forwards location data to Traccar using OsmAnd protocol (same as Traccar Client)
- **Webhook support** - sends data to n8n or other webhook endpoints
- **No authentication required** - uses native Traccar protocol
- Comprehensive error handling and logging
- Automatic reconnection on connection failures
- **Docker support** for easy deployment

## Quick Start with Docker

### 1. Clone and Setup
```bash
git clone <your-repo>
cd 2gis2traccar
```

### 2. Configure Environment
```bash
# Copy environment template
cp env.example .env

# Edit configuration with your secrets
nano .env
```

Update the `.env` file with your settings:
```env
# REQUIRED: 2GIS WebSocket URL with your token
TWOGIS_WS_URL=wss://zond.api.2gis.ru/api/1.1/user/ws?appVersion=6.31.0&channels=markers,sharing,routes&token=YOUR_TOKEN_HERE

# REQUIRED: Traccar server URL
TRACCAR_BASE_URL=http://your-traccar-server:5055

# Optional: Webhook Configuration
WEBHOOK_URL=webhook_url
WEBHOOK_TOKEN=your_bearer_token_here
WEBHOOK_TABLE_NAME=2gis_locations

# Optional: Logging level
LOG_LEVEL=INFO
```

### 3. Deploy
```bash
# Make deployment script executable
chmod +x deploy.sh

# Deploy with Docker
./deploy.sh
```

### 4. Monitor
```bash
# View logs
docker-compose logs -f

# Check status
docker-compose ps
```

## Manual Installation

1. Install Python dependencies:
```bash
pip install -r requirements.txt
```

2. Configure the script by creating a `.env` file:
   - Copy `env.example` to `.env`
   - Update `TWOGIS_WS_URL` with your 2GIS token
   - Update `TRACCAR_BASE_URL` with your Traccar server URL

## Usage

Run the script:
```bash
python 2gis_to_traccar.py
```

The script will:
1. Connect to the 2GIS WebSocket
2. Listen for `friendState` messages containing location data
3. Extract the friend ID and use it as device ID
4. Extract location coordinates, speed, battery level, and other data
5. Send the data to your Traccar server using OsmAnd protocol (HTTP POST with JSON format)
6. **Send the same data to your webhook endpoint** (if configured)

## Data Mapping

The script maps 2GIS data to OsmAnd JSON protocol format:

- **Location**: `lat`/`lon` → OsmAnd `coords.latitude`/`coords.longitude`
- **Speed**: Converts from m/s to km/h, then to m/s (OsmAnd JSON standard)
- **Course**: Uses `azimuth` field as `coords.heading`
- **Battery**: Maps to OsmAnd `battery.level` (decimal 0-1) and `battery.is_charging`
- **Accuracy**: Preserves accuracy data in `coords.accuracy`
- **Device ID**: Uses `{friend_id}` format as `device_id`
- **Movement Status**: Maps to `is_moving` boolean field and `activity.type` ("still" for stopped, "in_vehicle" for moving)
- **Extras**: Optimized to include only unique 2GIS data not already used in main structure:
  - `2gis_lastSeen`: Last seen timestamp (ISO format)
  - `2gis_stoppedAt`: When movement stopped (ISO format)
  - `2gis_locationId`: Location place ID
  - `2gis_locationUrl`: Direct 2GIS URL to the location
  - `2gis_regionId`: Region ID
  - `2gis_locationStatus`: Location status

## OsmAnd JSON Format

The script sends data to Traccar using the OsmAnd JSON format as specified in the [Traccar documentation](https://www.traccar.org/osmand/):

```json
{
  "location": {
    "timestamp": "2024-01-01T12:00:00.000Z",
    "coords": {
      "latitude": 55.7558,
      "longitude": 37.6176,
      "accuracy": 10.0,
      "speed": 1.97,
      "heading": 180.0,
      "altitude": 0
    },
    "is_moving": true,
    "odometer": 0,
    "event": "motionchange",
    "battery": {
      "level": 0.85,
      "is_charging": false
    },
    "activity": {
      "type": "in_vehicle"
    },
    "extras": {
      "2gis_lastSeen": "2024-01-01T12:00:00.000Z",
      "2gis_stoppedAt": "2024-01-01T11:30:00.000Z",
      "2gis_locationId": "70000001061605468",
      "2gis_locationUrl": "https://2gis.kz/almaty/firm/70000001061605468",
      "2gis_regionId": "67",
      "2gis_locationStatus": null
    }
  },
  "device_id": "friend_123"
}
```

## Webhook Data Format

When webhook is configured, the script sends the **raw 2GIS data** immediately after parsing the JSON message:

```json
{
  "tableName": "2gis_locations",
  "data": {
    "type": "friendState",
    "payload": {
      "id": "friend_123",
      "location": {
        "lat": 55.7558,
        "lon": 37.6176,
        "speed": 7.08,
        "azimuth": 180.0,
        "accuracy": 10.0
      },
      "battery": {
        "level": 0.85,
        "isCharging": false
      },
      "movement": {
        "status": "moving"
      }
    }
  }
}
```

The webhook request includes:
- **Authorization**: `Bearer {WEBHOOK_TOKEN}`
- **Content-Type**: `application/json`
- **Body**: JSON with `tableName` and raw `data` from 2GIS

## Docker Commands

```bash
# Build and start
docker-compose up -d

# View logs
docker-compose logs -f

# Stop service
docker-compose down

# Restart service
docker-compose restart

# Update and restart
docker-compose pull && docker-compose up -d

# Rebuild after code changes
docker-compose build && docker-compose up -d
```

## Configuration

### Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `TWOGIS_WS_URL` | ✅ Yes | - | 2GIS WebSocket URL with authentication token |
| `TRACCAR_BASE_URL` | ✅ Yes | - | Traccar server URL with OsmAnd port |
| `WEBHOOK_URL` | ❌ No | - | Webhook endpoint URL for sending data |
| `WEBHOOK_TOKEN` | ❌ No | - | Bearer token for webhook authentication |
| `WEBHOOK_TABLE_NAME` | ❌ No | `2gis_locations` | Table name for webhook data |
| `LOG_LEVEL` | ❌ No | `INFO` | Logging level (DEBUG, INFO, WARNING, ERROR) |
| `LOG_FILE` | ❌ No | `2gis2traccar.log` | Log file name |
| `RECONNECT_DELAY` | ❌ No | `30` | Reconnection delay in seconds |
| `MAX_RECONNECT_ATTEMPTS` | ❌ No | `10` | Maximum reconnection attempts |

All configuration is now done through environment variables for security. The `config.py` file validates that required variables are set and provides helpful error messages if they're missing.

## Logging

The script creates detailed logs in:
- Console output
- `2gis2traccar.log` file (or as configured)
- Docker logs: `docker-compose logs -f`

## Error Handling

- Automatic reconnection on WebSocket disconnection
- Retry logic for failed API calls
- Graceful handling of malformed JSON messages
- Comprehensive error logging

## Requirements

- Python 3.7+ (or Docker)
- aiohttp
- websockets
- Access to 2GIS WebSocket API
- Traccar server with OsmAnd protocol enabled

## Security Notes

- **All secrets are now in environment variables** - never commit `.env` files
- The 2GIS token in `TWOGIS_WS_URL` should be kept secure
- Consider using HTTPS for your Traccar server (though OsmAnd protocol works with HTTP)
- No credentials needed for Traccar - uses native protocol
- The script validates required environment variables on startup

## Troubleshooting

### Common Issues

1. **400 Bad Request**: Check that your Traccar server has OsmAnd protocol enabled and supports JSON format (Traccar 6.7.0+)
2. **Connection Refused**: Verify the Traccar server URL and port (usually 5055)
3. **WebSocket Connection Failed**: Check the 2GIS token in the WebSocket URL
4. **JSON Format Error**: Ensure your Traccar server supports OsmAnd JSON format (not just query parameters)

### Debug Mode

Enable debug logging to see detailed request information:
```bash
# In .env file
LOG_LEVEL=DEBUG
```

### Docker Debugging

```bash
# View container logs
docker-compose logs -f

# Execute shell in container
docker-compose exec 2gis2traccar /bin/bash

# Check container status
docker-compose ps
```
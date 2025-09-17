# 2GIS to Traccar Bridge

This Python script connects to the 2GIS WebSocket API, receives real-time location data, and forwards it to a Traccar GPS tracking server using the **OsmAnd protocol** (no authentication required!).

## Features

- Connects to 2GIS WebSocket API for real-time location updates
- Parses friendState messages with location, battery, and movement data
- Forwards location data to Traccar using OsmAnd protocol (same as Traccar Client)
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
3. Extract the friend ID and use it as device ID with "2gis_" prefix
4. Extract location coordinates, speed, battery level, and other data
5. Send the data to your Traccar server using OsmAnd protocol (HTTP GET with query parameters)

## Data Mapping

The script maps 2GIS data to OsmAnd protocol format:

- **Location**: `lat`/`lon` → OsmAnd `lat`/`lon` parameters
- **Speed**: Converts from m/s to km/h, then to knots (OsmAnd standard)
- **Course**: Uses `azimuth` field as `bearing` parameter
- **Battery**: Maps to OsmAnd `batt` parameter (percentage)
- **Accuracy**: Preserves accuracy data
- **Device ID**: Uses `2gis_{friend_id}` format
- **Movement Status**: Logged but not sent to Traccar

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

1. **400 Bad Request**: Check that your Traccar server has OsmAnd protocol enabled
2. **Connection Refused**: Verify the Traccar server URL and port (usually 5055)
3. **WebSocket Connection Failed**: Check the 2GIS token in the WebSocket URL

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
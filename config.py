"""
Configuration file for 2GIS to Traccar bridge
All secrets are now loaded from environment variables.
Copy env.example to .env and update the values.
"""

import os
import sys
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

def validate_required_env(env_var, description):
    """Validate that a required environment variable is set"""
    value = os.getenv(env_var)
    if not value:
        print(f"❌ Error: Required environment variable {env_var} is not set.")
        print(f"   {description}")
        print(f"   Please copy env.example to .env and update the values.")
        sys.exit(1)
    return value

# 2GIS WebSocket Configuration
TWOGIS_WS_URL = validate_required_env(
    "TWOGIS_WS_URL", 
    "2GIS WebSocket URL with authentication token"
)

# Traccar Server Configuration
# Use OsmAnd protocol - no authentication needed!
# Format: http://your-traccar-server:5055 (default Traccar port is 8082, but OsmAnd uses 5055)
TRACCAR_BASE_URL = validate_required_env(
    "TRACCAR_BASE_URL", 
    "Traccar server URL with OsmAnd port (usually 5055)"
)

# Logging Configuration
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FILE = os.getenv("LOG_FILE", "2gis2traccar.log")

# Reconnection settings
RECONNECT_DELAY = int(os.getenv("RECONNECT_DELAY", "30"))  # seconds
MAX_RECONNECT_ATTEMPTS = int(os.getenv("MAX_RECONNECT_ATTEMPTS", "10"))

# Webhook Configuration (optional)
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN")
WEBHOOK_TABLE_NAME = os.getenv("WEBHOOK_TABLE_NAME", "2gis_locations")

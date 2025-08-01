import json
import asyncio
import os

# Define paths for your JSON files
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')
if not os.path.exists(DATA_DIR):
    os.makedirs(DATA_DIR)

PRODUCTS_FILE = os.path.join(DATA_DIR, 'products.json')
ORDERS_FILE = os.path.join(DATA_DIR, 'orders.json')
USERS_FILE = os.path.join(DATA_DIR, 'users.json')
CONFIG_FILE = os.path.join(DATA_DIR, 'config.json')

# Ensure JSON files exist
for file_path, default_content in [
    (PRODUCTS_FILE, {
        "101": {
            "name": "Example Product 1", "description": "A sample product.",
            "price_inr": 100.00, "category": "General", "delivery_method": "DM",
            "renewal_period_days": None, "stock_status": "Available"
        }
    }),
    (ORDERS_FILE, {}),
    (USERS_FILE, {}),
    (CONFIG_FILE, {
        "staff_notifications_channel_id": None, "ticket_logs_channel_id": None,
        "renewal_alerts_channel_id": None, "ai_knowledge_base_path": "ai_knowledge_base.md",
        "ticket_panel_message_id": None, "ticket_panel_channel_id": None
    })
]:
    if not os.path.exists(file_path):
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(default_content, f, indent=4)

# Simple lock for thread-safe JSON operations
json_locks = {file: asyncio.Lock() for file in [PRODUCTS_FILE, ORDERS_FILE, USERS_FILE, CONFIG_FILE]}

async def load_json(file_path):
    """Loads data from a JSON file asynchronously."""
    async with json_locks[file_path]:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {} # Return empty dict if not found or corrupted

async def save_json(file_path, data):
    """Saves data to a JSON file asynchronously."""
    async with json_locks[file_path]:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=4)

# --- Product Functions ---
async def get_products():
    return await load_json(PRODUCTS_FILE)
async def save_products(products):
    await save_json(PRODUCTS_FILE, products)

# --- Order Functions ---
async def get_orders():
    return await load_json(ORDERS_FILE)
async def save_orders(orders):
    await save_json(ORDERS_FILE, orders)

# --- User Functions ---
async def get_users():
    return await load_json(USERS_FILE)
async def save_users(users):
    await save_json(USERS_FILE, users)

# --- Config Functions ---
async def get_config():
    return await load_json(CONFIG_FILE)
async def save_config(config):
    await save_json(CONFIG_FILE, config)
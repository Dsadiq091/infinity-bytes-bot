import asyncio
import json
import os
import psycopg2
from psycopg2 import Error
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# --- Database Configuration (Get from environment variable) ---
DATABASE_URL = os.getenv('DATABASE_URL')

if not DATABASE_URL:
    print("Error: DATABASE_URL environment variable not set. Cannot connect to database for migration.")
    exit(1)

# --- Database Connection Function ---
async def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        # Test connection
        with conn.cursor() as cursor:
            cursor.execute("SELECT 1")
        print(f"Connected to database via DATABASE_URL.")
        return conn
    except Error as e:
        print(f"Error connecting to database: {e}")
        return None

# --- JSON Loading Helper (Original JSON loading from your bot's data folder) ---
DATA_FOLDER = 'data'

async def load_json_file(filename):
    filepath = os.path.join(DATA_FOLDER, f'{filename}.json')
    if not os.path.exists(filepath):
        print(f"Warning: {filepath} not found. Skipping.")
        if filename == 'scheduled_tasks':
            return []
        return {}
    with open(filepath, 'r', encoding='utf-8') as f:
        try:
            content = f.read()
            if not content.strip():
                print(f"Warning: {filepath} is empty. Returning empty dict/list.")
                if filename == 'scheduled_tasks':
                    return []
                return {}
            return json.loads(content)
        except json.JSONDecodeError:
            print(f"Error decoding {filepath}. Returning empty dict/list.")
            if filename == 'scheduled_tasks':
                return []
            return {}

# --- Migration Functions (Supabase PostgreSQL adjustments) ---
# These functions remain the same as the previous full migration script,
# as the internal SQL logic is independent of the connection method (URL vs discrete params)

async def migrate_users(conn):
    print("Migrating users data...")
    users_data = await load_json_file('users')
    if not users_data:
        print("No users data to migrate.")
        return
    cursor = conn.cursor()
    sql = """
    INSERT INTO users (discord_id, points, wallet_balance, created_at, last_updated)
    VALUES (%s, %s, %s, NOW(), NOW())
    ON CONFLICT (discord_id) DO UPDATE
    SET points=EXCLUDED.points, wallet_balance=EXCLUDED.wallet_balance, last_updated=NOW();
    """
    values = []
    for user_id_str, data in users_data.items():
        points = data.get('points', 0)
        wallet_balance = data.get('wallet_balance', 0.00)
        values.append((user_id_str, points, wallet_balance))
    if values:
        try:
            cursor.executemany(sql, values)
            conn.commit()
            print(f"Successfully migrated {len(values)} users.")
        except Error as e:
            print(f"Error migrating users: {e}")
            conn.rollback()
    cursor.close()

async def migrate_products(conn):
    print("Migrating products data...")
    products_data = await load_json_file('products')
    if not products_data:
        print("No products data to migrate.")
        return
    cursor = conn.cursor()
    sql = """
    INSERT INTO products (product_id, name, description, price, stock, emoji, image_url, renewal_period_days, is_active, created_at, last_updated)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, TRUE, NOW(), NOW())
    ON CONFLICT (product_id) DO UPDATE
    SET name=EXCLUDED.name, description=EXCLUDED.description, price=EXCLUDED.price,
        stock=EXCLUDED.stock, emoji=EXCLUDED.emoji, image_url=EXCLUDED.image_url,
        renewal_period_days=EXCLUDED.renewal_period_days, last_updated=NOW();
    """
    values = []
    for product_id, data in products_data.items():
        price = float(data.get('price')) if data.get('price') is not None else None
        stock = data.get('stock', -1)
        renewal_period_days = data.get('renewal_period_days')
        values.append((
            product_id, data.get('name'), data.get('description'),
            price, stock, data.get('emoji'), data.get('image_url'),
            renewal_period_days
        ))
    if values:
        try:
            cursor.executemany(sql, values)
            conn.commit()
            print(f"Successfully migrated {len(values)} products.")
        except Error as e:
            print(f"Error migrating products: {e}")
            conn.rollback()
    cursor.close()

async def migrate_orders(conn):
    print("Migrating orders data...")
    orders_data = await load_json_file('orders')
    if not orders_data:
        print("No orders data to migrate.")
        return
    cursor = conn.cursor()
    order_sql = """
    INSERT INTO orders (order_id, user_discord_id, items_json, status, discount, discount_reason, gift_recipient_discord_id, timestamp, channel_id, payment_method, notes, referral_code_used, referrer_discord_id, created_at, last_updated)
    VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
    ON CONFLICT (order_id) DO UPDATE
    SET
        user_discord_id = EXCLUDED.user_discord_id, items_json = EXCLUDED.items_json, status = EXCLUDED.status,
        discount = EXCLUDED.discount, discount_reason = EXCLUDED.discount_reason,
        gift_recipient_discord_id = EXCLUDED.gift_recipient_discord_id,
        timestamp = EXCLUDED.timestamp, channel_id = EXCLUDED.channel_id,
        payment_method = EXCLUDED.payment_method, notes = EXCLUDED.notes,
        referral_code_used = EXCLUDED.referral_code_used, referrer_discord_id = EXCLUDED.referrer_discord_id,
        last_updated = NOW();
    """
    order_values_list = []
    for order_id, data in orders_data.items():
        timestamp_str = data.get('timestamp')
        timestamp_dt = None
        if timestamp_str:
            try:
                timestamp_dt = datetime.fromisoformat(timestamp_str)
            except ValueError:
                print(f"Warning: Malformed timestamp for order {order_id}: {timestamp_str}. Using NULL.")
        referral_info = data.get('referral_info', {})
        referral_code = referral_info.get('code')
        referrer_discord_id = str(referral_info.get('referrer_id')) if referral_info.get('referrer_id') else None
        order_values_list.append((
            order_id,
            str(data.get('user_id')),
            json.dumps(data.get('items', {})),
            data.get('status'),
            data.get('discount', 0.0),
            data.get('discount_reason', 'No Discount'),
            str(data.get('gift_recipient_id')) if data.get('gift_recipient_id') else None,
            timestamp_dt,
            str(data.get('channel_id')) if data.get('channel_id') else None,
            data.get('payment_method'),
            data.get('notes'),
            referral_code,
            referrer_discord_id
        ))
    if order_values_list:
        try:
            cursor.executemany(order_sql, order_values_list)
            conn.commit()
            print(f"Successfully migrated {len(order_values_list)} orders.")
        except Error as e:
            print(f"Error migrating orders: {e}")
            conn.rollback()
    cursor.close()

async def migrate_discounts(conn):
    print("Migrating discounts data...")
    discounts_data = await load_json_file('discounts')
    if not discounts_data:
        print("No discounts data to migrate.")
        return
    cursor = conn.cursor()
    sql = """
    INSERT INTO discounts (code, type, discount_inr, max_uses, uses, expires_at, is_active, generated_by_discord_id, created_at, last_updated)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
    ON CONFLICT (code) DO UPDATE
    SET type=EXCLUDED.type, discount_inr=EXCLUDED.discount_inr, max_uses=EXCLUDED.max_uses,
        uses=EXCLUDED.uses, expires_at=EXCLUDED.expires_at, is_active=EXCLUDED.is_active,
        generated_by_discord_id=EXCLUDED.generated_by_discord_id, last_updated=NOW();
    """
    values = []
    for code, data in discounts_data.items():
        max_uses_val = data.get('max_uses')
        if max_uses_val == float('inf'):
            max_uses_val = 0
        expires_at_dt = None
        if data.get('expires_at'):
            try:
                expires_at_dt = datetime.fromisoformat(data['expires_at'])
            except ValueError:
                print(f"Warning: Malformed expires_at for discount {code}: {data['expires_at']}. Using NULL.")
        values.append((
            code,
            data.get('type'),
            float(data.get('discount_inr', 0.0)),
            max_uses_val,
            data.get('uses', 0),
            expires_at_dt,
            data.get('is_active', True),
            str(data.get('generated_by')) if data.get('generated_by') else None
        ))
    if values:
        try:
            cursor.executemany(sql, values)
            conn.commit()
            print(f"Successfully migrated {len(values)} discounts.")
        except Error as e:
            print(f"Error migrating discounts: {e}")
            conn.rollback()
    cursor.close()

async def migrate_referrals(conn):
    print("Migrating referrals data...")
    referrals_data = await load_json_file('referrals')
    if not referrals_data:
        print("No referrals data to migrate.")
        return
    cursor = conn.cursor()
    sql = """
    INSERT INTO referrals (code, referrer_discord_id, created_at)
    VALUES (%s, %s, NOW())
    ON CONFLICT (code) DO UPDATE
    SET referrer_discord_id=EXCLUDED.referrer_discord_id;
    """
    values = []
    for code, referrer_id in referrals_data.items():
        values.append((code, str(referrer_id)))
    if values:
        try:
            cursor.executemany(sql, values)
            conn.commit()
            print(f"Successfully migrated {len(values)} referrals.")
        except Error as e:
            print(f"Error migrating referrals: {e}")
            conn.rollback()
    cursor.close()

async def migrate_counters(conn):
    print("Migrating counters data...")
    counters_data = await load_json_file('counters')
    if not counters_data:
        print("No counters data to migrate.")
        return
    cursor = conn.cursor()
    sql = """
    INSERT INTO counters (counter_name, last_value, last_updated)
    VALUES (%s, %s, NOW())
    ON CONFLICT (counter_name) DO UPDATE
    SET last_value=EXCLUDED.last_value, last_updated=NOW();
    """
    values = []
    for name, value in counters_data.items():
        values.append((name, value))
    if values:
        try:
            cursor.executemany(sql, values)
            conn.commit()
            print(f"Successfully migrated {len(values)} counters.")
        except Error as e:
            print(f"Error migrating counters: {e}")
            conn.rollback()
    cursor.close()

async def migrate_scheduled_tasks(conn):
    print("Migrating scheduled tasks data...")
    scheduled_tasks_data = await load_json_file('scheduled_tasks')
    if not scheduled_tasks_data:
        print("No scheduled tasks data to migrate.")
        return
    cursor = conn.cursor()
    sql = """
    INSERT INTO scheduled_tasks (task_id, due_at, channel_id, message, created_at)
    VALUES (%s, %s, %s, %s, NOW())
    ON CONFLICT (task_id) DO UPDATE
    SET due_at=EXCLUDED.due_at, channel_id=EXCLUDED.channel_id, message=EXCLUDED.message;
    """
    values = []
    for task in scheduled_tasks_data:
        due_at_dt = None
        if task.get('due_at'):
            try:
                due_at_dt = datetime.fromisoformat(task['due_at'])
            except ValueError:
                print(f"Warning: Malformed due_at for task {task.get('task_id')}: {task['due_at']}. Using NULL.")
        values.append((
            task.get('task_id'),
            due_at_dt,
            str(task.get('channel_id')) if task.get('channel_id') else None,
            task.get('message')
        ))
    if values:
        try:
            cursor.executemany(sql, values)
            conn.commit()
            print(f"Successfully migrated {len(values)} scheduled tasks.")
        except Error as e:
            print(f"Error migrating scheduled tasks: {e}")
            conn.rollback()
    cursor.close()

async def migrate_notifications(conn):
    print("Migrating notifications data...")
    notifications_data = await load_json_file('notifications')
    if not notifications_data:
        print("No notifications data to migrate.")
        return
    cursor = conn.cursor()
    sql = """
    INSERT INTO notifications (product_id, user_discord_id, created_at)
    VALUES (%s, %s, NOW())
    ON CONFLICT (product_id, user_discord_id) DO NOTHING;
    """
    values = []
    for product_id, user_ids in notifications_data.items():
        for user_id in user_ids:
            values.append((product_id, str(user_id)))
    if values:
        try:
            cursor.executemany(sql, values)
            conn.commit()
            print(f"Successfully migrated {len(values)} notifications.")
        except Error as e:
            print(f"Error migrating notifications: {e}")
            conn.rollback()
    cursor.close()

async def migrate_store_state(conn):
    print("Migrating store_state data...")
    store_state_data = await load_json_file('store_state')
    if not store_state_data:
        print("No store state data to migrate.")
        return
    cursor = conn.cursor()
    sql = """
    INSERT INTO config (key_name, value, last_updated)
    VALUES (%s, %s, NOW())
    ON CONFLICT (key_name) DO UPDATE
    SET value=EXCLUDED.value, last_updated=NOW();
    """
    values = []
    for key, value in store_state_data.items():
        val_to_save = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        values.append((key, val_to_save))
    if values:
        try:
            cursor.executemany(sql, values)
            conn.commit()
            print(f"Successfully migrated {len(values)} store state entries into config.")
        except Error as e:
            print(f"Error migrating store state: {e}")
            conn.rollback()
    cursor.close()

async def main_migration_script():
    conn = await get_db_connection()
    if not conn:
        print("Failed to get database connection. Aborting migration.")
        return
    print("\n--- Starting Data Migration ---")
    await migrate_users(conn)
    await migrate_products(conn)
    await migrate_orders(conn)
    await migrate_discounts(conn)
    await migrate_referrals(conn)
    await migrate_counters(conn)
    await migrate_scheduled_tasks(conn)
    await migrate_notifications(conn)
    await migrate_store_state(conn)
    print("\n--- Data Migration Complete ---")
    if conn and not conn.closed:
        conn.close()
        print("Database connection closed.")

if __name__ == "__main__":
    asyncio.run(main_migration_script())
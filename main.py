import sys
import discord
from discord.ext import commands
import os
import json
from dotenv import load_dotenv
import asyncio
import psycopg2
from psycopg2 import Error, extras
from datetime import datetime

load_dotenv()

db_lock = asyncio.Lock()

async def _load_data_from_db(self, filename_prefix: str):
    async with db_lock:
        if not self.db_connection or self.db_connection.closed:
            print(f"Database not connected. Cannot load data for {filename_prefix}. Returning empty dict/list.")
            if filename_prefix in ['scheduled_tasks']:
                return []
            return {}

        cursor = self.db_connection.cursor(cursor_factory=extras.DictCursor)
        data = {}
        list_data = []

        try:
            if filename_prefix == 'products':
                cursor.execute("SELECT product_id, name, description, price, stock, emoji, image_url, renewal_period_days FROM products")
                for row in cursor:
                    data[row['product_id']] = {
                        'name': row['name'],
                        'description': row['description'],
                        'price': float(row['price']) if row['price'] is not None else None,
                        'stock': row['stock'],
                        'emoji': row['emoji'],
                        'image_url': row['image_url'],
                        'renewal_period_days': row['renewal_period_days']
                    }
                return data

            elif filename_prefix == 'orders':
                cursor.execute("SELECT order_id, user_discord_id, items_json, status, discount, discount_reason, gift_recipient_discord_id, timestamp, channel_id, payment_method, notes, referral_code_used, referrer_discord_id FROM orders")
                for order_row in cursor:
                    order_id = order_row['order_id']
                    data[order_id] = {
                        'user_id': int(order_row['user_discord_id']),
                        'items': order_row['items_json'],
                        'status': order_row['status'],
                        'discount': float(order_row['discount']),
                        'discount_reason': order_row['discount_reason'],
                        'gift_recipient_id': int(order_row['gift_recipient_discord_id']) if order_row['gift_recipient_discord_id'] else None,
                        'timestamp': order_row['timestamp'].isoformat() if order_row['timestamp'] else None,
                        'channel_id': str(order_row['channel_id']) if order_row['channel_id'] else None,
                        'payment_method': order_row['payment_method'],
                        'notes': order_row['notes'],
                        'referral_code_used': order_row['referral_code_used'],
                        'referrer_discord_id': str(order_row['referrer_discord_id']) if order_row['referrer_discord_id'] else None
                    }
                return data

            elif filename_prefix == 'users':
                cursor.execute("SELECT discord_id, points, wallet_balance FROM users")
                for row in cursor:
                    data[str(row['discord_id'])] = {'points': row['points'], 'wallet_balance': float(row['wallet_balance'])}
                return data

            elif filename_prefix == 'discounts':
                cursor.execute("SELECT code, type, discount_inr, max_uses, uses, expires_at, is_active, generated_by_discord_id FROM discounts")
                for row in cursor:
                    max_uses = float('inf') if row['max_uses'] == 0 else row['max_uses']

                    data[row['code']] = {
                        'type': row['type'],
                        'discount_inr': float(row['discount_inr']),
                        'max_uses': max_uses,
                        'uses': row['uses'],
                        'expires_at': row['expires_at'].isoformat() if row['expires_at'] else None,
                        'is_active': row['is_active'],
                        'used': False,
                        'generated_by': str(row['generated_by_discord_id']) if row['generated_by_discord_id'] else None
                    }
                return data

            elif filename_prefix == 'referrals':
                cursor.execute("SELECT code, referrer_discord_id FROM referrals")
                for row in cursor:
                    data[row['code']] = str(row['referrer_discord_id'])
                return data

            elif filename_prefix == 'counters':
                cursor.execute("SELECT counter_name, last_value FROM counters")
                for row in cursor:
                    data[row['counter_name']] = row['last_value']
                return data

            elif filename_prefix == 'scheduled_tasks':
                cursor.execute("SELECT task_id, due_at, channel_id, message FROM scheduled_tasks")
                for row in cursor:
                    list_data.append({
                        'task_id': row['task_id'],
                        'due_at': row['due_at'].isoformat() if row['due_at'] else None,
                        'channel_id': str(row['channel_id']),
                        'message': row['message']
                    })
                return list_data

            elif filename_prefix == 'notifications':
                cursor.execute("SELECT product_id, user_discord_id FROM notifications")
                for row in cursor:
                    data.setdefault(row['product_id'], []).append(int(row['user_discord_id']))
                return data

            elif filename_prefix == 'store_state':
                cursor.execute("SELECT key_name, value FROM config")
                for row in cursor:
                    try:
                        data[row['key_name']] = json.loads(row['value'])
                    except json.JSONDecodeError:
                        data[row['key_name']] = row['value']
                return data

            elif filename_prefix == 'config':
                return self.config

            else:
                print(f"Unknown filename prefix for loading: {filename_prefix}. Returning empty dict.")
                return {}

        except Error as e:
            print(f"Error loading {filename_prefix} from database: {e}")
            if filename_prefix in ['scheduled_tasks']:
                return []
            return {}
        finally:
            cursor.close()

async def _save_data_to_db(self, filename_prefix: str, data):
    async with db_lock:
        if not self.db_connection or self.db_connection.closed:
            print(f"Database not connected. Cannot save data for {filename_prefix}.")
            return

        cursor = self.db_connection.cursor()

        try:
            if filename_prefix == 'products':
                products_to_insert_update = []
                for product_id, p_data in data.items():
                    price_val = float(p_data.get('price')) if p_data.get('price') is not None else None
                    renewal_val = int(p_data.get('renewal_period_days')) if p_data.get('renewal_period_days') is not None else None
                    products_to_insert_update.append((
                        product_id, p_data.get('name'), p_data.get('description'),
                        price_val, p_data.get('stock', -1), p_data.get('emoji'),
                        p_data.get('image_url'), renewal_val
                    ))

                if products_to_insert_update:
                    sql = """
                    INSERT INTO products (product_id, name, description, price, stock, emoji, image_url, renewal_period_days, created_at, last_updated)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (product_id) DO UPDATE
                    SET name=EXCLUDED.name, description=EXCLUDED.description, price=EXCLUDED.price,
                        stock=EXCLUDED.stock, emoji=EXCLUDED.emoji, image_url=EXCLUDED.image_url,
                        renewal_period_days=EXCLUDED.renewal_period_days, last_updated=NOW();
                    """
                    cursor.executemany(sql, products_to_insert_update)

                existing_pids_in_db_cursor = self.db_connection.cursor()
                existing_pids_in_db_cursor.execute("SELECT product_id FROM products")
                existing_pids = {row[0] for row in existing_pids_in_db_cursor.fetchall()}
                pids_to_delete = existing_pids - set(data.keys())
                if pids_to_delete:
                    delete_sql = "DELETE FROM products WHERE product_id = ANY(%s);"
                    cursor.execute(delete_sql, (list(pids_to_delete),))
                existing_pids_in_db_cursor.close()

                self.db_connection.commit()
                print(f"Saved {len(data)} products to database (updated/added). Deleted {len(pids_to_delete)} removed products.")


            elif filename_prefix == 'orders':
                existing_order_ids = set()
                with self.db_connection.cursor() as temp_cursor: # Use a separate cursor to avoid interference
                    temp_cursor.execute("SELECT order_id FROM orders")
                    existing_order_ids = {row[0] for row in temp_cursor.fetchall()}

                orders_to_insert_update = []

                for order_id, o_data in data.items():
                    timestamp_str = o_data.get('timestamp')
                    timestamp_dt = datetime.fromisoformat(timestamp_str) if timestamp_str else None

                    user_discord_id = str(o_data['user_id'])
                    gift_recipient_discord_id = str(o_data['gift_recipient_id']) if o_data.get('gift_recipient_id') else None
                    referral_code = o_data.get('referral_info', {}).get('code')
                    referrer_discord_id = str(o_data.get('referral_info', {}).get('referrer_id')) if o_data.get('referral_info', {}).get('referrer_id') else None
                    channel_id_str = str(o_data['channel_id']) if o_data.get('channel_id') else None

                    orders_to_insert_update.append((
                        order_id, user_discord_id, json.dumps(o_data['items']),
                        o_data['status'], o_data['discount'],
                        o_data.get('discount_reason', 'No Discount'), gift_recipient_discord_id,
                        timestamp_dt, channel_id_str, o_data.get('payment_method'), o_data.get('notes'),
                        referral_code, referrer_discord_id
                    ))

                if orders_to_insert_update:
                    order_sql = """
                    INSERT INTO orders (order_id, user_discord_id, items_json, status, discount, discount_reason, gift_recipient_discord_id, timestamp, channel_id, payment_method, notes, referral_code_used, referrer_discord_id, created_at, last_updated)
                    VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (order_id) DO UPDATE
                    SET user_discord_id=EXCLUDED.user_discord_id, items_json=EXCLUDED.items_json, status=EXCLUDED.status,
                        discount=EXCLUDED.discount, discount_reason=EXCLUDED.discount_reason,
                        gift_recipient_discord_id=EXCLUDED.gift_recipient_discord_id, timestamp=EXCLUDED.timestamp,
                        channel_id=EXCLUDED.channel_id, payment_method=EXCLUDED.payment_method, notes=EXCLUDED.notes,
                        referral_code_used=EXCLUDED.referral_code_used, referrer_discord_id=EXCLUDED.referrer_discord_id,
                        last_updated=NOW();
                    """
                    cursor.executemany(order_sql, orders_to_insert_update)

                pids_to_delete_from_db = existing_order_ids - set(data.keys())
                if pids_to_delete_from_db:
                    delete_order_sql = "DELETE FROM orders WHERE order_id = ANY(%s);"
                    cursor.execute(delete_order_sql, (list(pids_to_delete_from_db),))

                self.db_connection.commit()
                print(f"Saved {len(data)} orders to database (updated/added). Deleted {len(pids_to_delete_from_db)} removed orders.")


            elif filename_prefix == 'users':
                users_to_insert_update = []
                for user_id_str, u_data in data.items():
                    users_to_insert_update.append((user_id_str, u_data['points'], u_data.get('wallet_balance', 0.00)))
                if users_to_insert_update:
                    sql = """
                    INSERT INTO users (discord_id, points, wallet_balance, created_at, last_updated)
                    VALUES (%s, %s, %s, NOW(), NOW())
                    ON CONFLICT (discord_id) DO UPDATE
                    SET points=EXCLUDED.points, wallet_balance=EXCLUDED.wallet_balance, last_updated=NOW();
                    """
                    cursor.executemany(sql, users_to_insert_update)

                existing_uids_in_db_cursor = self.db_connection.cursor()
                existing_uids_in_db_cursor.execute("SELECT discord_id FROM users")
                existing_uids = {row[0] for row in existing_uids_in_db_cursor.fetchall()}
                uids_to_delete = existing_uids - set(data.keys())
                if uids_to_delete:
                    delete_sql = "DELETE FROM users WHERE discord_id = ANY(%s);"
                    cursor.execute(delete_sql, (list(uids_to_delete),))
                existing_uids_in_db_cursor.close()

                self.db_connection.commit()
                print(f"Saved {len(data)} users to database (updated/added). Deleted {len(uids_to_delete)} removed users.")


            elif filename_prefix == 'discounts':
                discounts_to_insert = []
                for code, d_data in data.items():
                    max_uses_val = 0 if d_data['max_uses'] == float('inf') else d_data['max_uses']
                    expires_at_dt = datetime.fromisoformat(d_data['expires_at']) if d_data['expires_at'] else None
                    discounts_to_insert.append((
                        code, d_data['type'], d_data['discount_inr'], max_uses_val, d_data['uses'],
                        expires_at_dt, bool(d_data.get('is_active', True)),
                        str(d_data.get('generated_by')) if d_data.get('generated_by') else None
                    ))
                if discounts_to_insert:
                    sql = """
                    INSERT INTO discounts (code, type, discount_inr, max_uses, uses, expires_at, is_active, generated_by_discord_id, created_at, last_updated)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), NOW())
                    ON CONFLICT (code) DO UPDATE
                    SET type=EXCLUDED.type, discount_inr=EXCLUDED.discount_inr, max_uses=EXCLUDED.max_uses,
                        uses=EXCLUDED.uses, expires_at=EXCLUDED.expires_at, is_active=EXCLUDED.is_active,
                        generated_by_discord_id=EXCLUDED.generated_by_discord_id, last_updated=NOW();
                    """
                    cursor.executemany(sql, discounts_to_insert)
                self.db_connection.commit()
                print(f"Saved {len(data)} discounts to database.")


            elif filename_prefix == 'referrals':
                cursor.execute("DELETE FROM referrals")
                referrals_to_insert = []
                for code, referrer_id in data.items():
                    referrals_to_insert.append((code, str(referrer_id)))
                if referrals_to_insert:
                    sql = "INSERT INTO referrals (code, referrer_discord_id, created_at) VALUES (%s, %s, NOW());"
                    cursor.executemany(sql, referrals_to_insert)
                self.db_connection.commit()
                print(f"Saved {len(data)} referrals to database.")

            elif filename_prefix == 'counters':
                counters_to_insert_update = []
                for counter_name, value in data.items():
                    counters_to_insert_update.append((counter_name, value))
                if counters_to_insert_update:
                    sql = """
                    INSERT INTO counters (counter_name, last_value, last_updated)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (counter_name) DO UPDATE
                    SET last_value=EXCLUDED.last_value, last_updated=NOW();
                    """
                    cursor.executemany(sql, counters_to_insert_update)
                self.db_connection.commit()
                print(f"Saved {len(data)} counters to database.")

            elif filename_prefix == 'scheduled_tasks':
                cursor.execute("DELETE FROM scheduled_tasks")
                tasks_to_insert = []
                for task in data:
                    due_at_dt = datetime.fromisoformat(task['due_at']) if task['due_at'] else None
                    tasks_to_insert.append((task['task_id'], due_at_dt, str(task['channel_id']), task['message']))
                if tasks_to_insert:
                    sql = "INSERT INTO scheduled_tasks (task_id, due_at, channel_id, message, created_at) VALUES (%s, %s, %s, %s, NOW());"
                    cursor.executemany(sql, tasks_to_insert)
                self.db_connection.commit()
                print(f"Saved {len(data)} scheduled tasks to database.")

            elif filename_prefix == 'notifications':
                cursor.execute("DELETE FROM notifications")
                notifications_to_insert = []
                for product_id, user_ids in data.items():
                    for user_id in user_ids:
                        notifications_to_insert.append((product_id, str(user_id)))
                if notifications_to_insert:
                    sql = "INSERT INTO notifications (product_id, user_discord_id, created_at) VALUES (%s, %s, NOW());"
                    cursor.executemany(sql, notifications_to_insert)
                self.db_connection.commit()
                print(f"Saved {sum(len(v) for v in data.values())} notifications to database.")

            elif filename_prefix == 'store_state':
                state_to_insert_update = []
                for key, value in data.items():
                    val_to_save = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
                    state_to_insert_update.append((key, val_to_save))
                if state_to_insert_update:
                    sql = """
                    INSERT INTO config (key_name, value, last_updated)
                    VALUES (%s, %s, NOW())
                    ON CONFLICT (key_name) DO UPDATE
                    SET value=EXCLUDED.value, last_updated=NOW();
                    """
                    cursor.executemany(sql, state_to_insert_update)
                self.db_connection.commit()
                print(f"Saved {len(data)} store state entries to database.")

            elif filename_prefix == 'config':
                pass # Main config still managed by JSON file.

            else:
                print(f"Unknown filename prefix for saving: {filename_prefix}. No data saved.")

        except Error as e:
            self.db_connection.rollback()
            print(f"Error saving {filename_prefix} to database: {e}")
        finally:
            cursor.close()

class YourStoreBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default(); intents.message_content = True; intents.members = True
        with open('config.json', 'r') as f: self.config = json.load(f)
        super().__init__(command_prefix=self.config['prefix'], intents=intents)
        self.synced = False
        self.active_tickets = {}
        self.db_connection = None

    async def connect_db(self):
        database_url = os.getenv("DATABASE_URL") # Get connection string from .env
        if not database_url:
            print("❌ DATABASE_URL environment variable not set. Cannot connect to database.")
            return

        try:
            # psycopg2.connect can parse a full connection URL
            self.db_connection = psycopg2.connect(database_url)
            # Test connection with a simple query
            with self.db_connection.cursor() as cursor:
                cursor.execute("SELECT 1")
            print(f"✅ Connected to Supabase PostgreSQL database via URL.")
        except Error as e:
            print(f"❌ Error connecting to Supabase PostgreSQL database: {e}")
            self.db_connection = None

    async def close_db(self):
        if self.db_connection and not self.db_connection.closed:
            self.db_connection.close()
            print("PostgreSQL connection closed.")

    async def setup_hook(self):
        print("Starting setup_hook...")
        await self.connect_db()
        print("Database connection attempted.")

        self.load_json = _load_data_from_db.__get__(self, self.__class__)
        self.save_json = _save_data_to_db.__get__(self, self.__class__)

        cogs_to_load = [f[:-3] for f in os.listdir('./cogs') if f.endswith('.py')]
        for cog in cogs_to_load:
            try:
                await self.load_extension(f'cogs.{cog}')
                print(f'Loaded cog: {cog}')
            except Exception as e: print(f'Failed to load cog {cog}: {e}')

        initial_products_for_sc_view = await self.load_json('products')

        from cogs.setup import TicketPanelView
        from cogs.ticket_system import ShoppingCartView, StaffTicketView, StaffClaimedView, SupportTicketView, TranscriptInstructionsView
        from cogs.marketing import FlashSaleView

        self.add_view(TicketPanelView(bot=self))
        self.add_view(ShoppingCartView(self, products=initial_products_for_sc_view))
        self.add_view(StaffTicketView(self))
        self.add_view(StaffClaimedView(self))
        self.add_view(FlashSaleView(self, "dummy_product_id", "Dummy Product", 0.0))
        self.add_view(TranscriptInstructionsView())

        print("Setup hook completed.")

    async def on_ready(self):
        print(f'--- Logged in as {self.user} (ID: {self.user.id}) ---')
        if not self.synced:
            guild_id = os.getenv("DISCORD_GUILD_ID")
            if guild_id:
                try:
                    guild = discord.Object(id=int(guild_id))
                    self.tree.copy_global_to(guild=guild)
                    synced = await self.tree.sync(guild=guild)
                    print(f"✅ Synced {len(synced)} slash commands.")
                    self.synced = True
                except Exception as e: print(f"❌ Failed to sync commands: {e}")
            else:
                print("⚠️ DISCORD_GUILD_ID not found in .env. Syncing global commands.")
                synced = await self.tree.sync()
                print(f"✅ Synced {len(synced)} global slash commands.")
                self.synced = True

bot = YourStoreBot()

async def main_run():
    BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN")
    if not BOT_TOKEN:
        print("Error: DISCORD_BOT_TOKEN environment variable not set.")
        sys.exit(1)

    try:
        await bot.start(BOT_TOKEN)
    except KeyboardInterrupt:
        print("Bot shutdown initiated by KeyboardInterrupt.")
    except Exception as e:
        print(f"An unexpected error occurred during bot runtime: {e}")
    finally:
        await bot.close_db()

if __name__ == "__main__":
    asyncio.run(main_run())
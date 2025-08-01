# cogs/marketing.py
import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import re
import secrets
import string

# Import the custom checks
from utils.checks import is_owner

# Define the autocomplete helper function for products.
# This needs to be outside the Marketing cog class or a static method if it's only for this cog,
# or better, an instance method of Marketing if it also needs Marketing's self.bot.
# For cross-cog autocomplete, it's usually defined once (e.g., in ProductManagement)
# and then referenced. The below pattern is the robust way to reference it.
async def product_autocomplete_from_product_management(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[str]]:
    """Autocomplete for product IDs, fetching from ProductManagement cog."""
    # Access the bot instance via interaction.client
    bot = interaction.client
    # Get the ProductManagement cog instance
    product_management_cog = bot.get_cog('ProductManagement')
    if product_management_cog:
        # Call the actual product_autocomplete method from ProductManagement cog
        return await product_management_cog.product_autocomplete(interaction, current)
    return []


# --- UI View for the Flash Sale ---
class FlashSaleView(discord.ui.View):
    def __init__(self, bot, product_id: str, product_name: str, sale_price: float):
        super().__init__(timeout=None) # Set timeout to None to make this view persistent across bot restarts
        self.bot = bot
        self.product_id = product_id
        self.product_name = product_name
        self.sale_price = sale_price
        
        # State to track if the sale has been claimed (in-memory).
        # For true persistence of claims across bot restarts, this needs to be saved to a file (e.g., store_state.json).
        self.claimed = False
        self.winner_id = None
        self.custom_id = f"flash_sale_view_{product_id}" # Unique custom_id for persistence

        # When the view is loaded from persistence, its buttons are recreated.
        # We need to explicitly add them back here.
        # Check if they are already added to prevent duplicates if __init__ is called multiple times.
        if not any(item.custom_id == "flash_sale_buy_now" for item in self.children):
            self.add_item(discord.ui.Button(label="Buy Now!", style=discord.ButtonStyle.success, emoji="⚡", custom_id="flash_sale_buy_now"))
            
        # Immediately disable if already claimed (this state is volatile on restart without file persistence)
        # A robust solution would load self.claimed from a file here.
        # For this example, if the bot restarts, the sale might become available again briefly if not in store_state.
        if self.claimed:
            self.children[0].disabled = True
            self.children[0].label = "Claimed!"

    # This classmethod is a placeholder for how Discord.py *could* re-instantiate persistent views.
    # However, direct instantiation in main.py with dummy values is usually what's done for simplicity,
    # and then the state is updated when an interaction happens or from a persistent file.
    # The actual state (like 'claimed') needs to be loaded from your data files upon bot start.
    @classmethod
    async def from_message(cls, bot_instance, message: discord.Message):
        # This method attempts to reconstruct the view's initial state from a message.
        # For full persistence, you would store the actual product_id, product_name, sale_price,
        # and most importantly, whether it was claimed and by whom, in your JSON store_state.json.
        
        embed = message.embeds[0] if message.embeds else None
        if not embed or "FLASH SALE!" not in embed.title:
            return None # Not a flash sale message

        product_id = "unknown_flash_sale_product_id" # Default/Fallback
        product_name = "Unknown Product"
        sale_price = 0.0

        # Try to extract data from embed fields and footer
        for field in embed.fields:
            if field.name == "Product":
                product_name = field.value
            elif field.name == "SALE PRICE":
                try:
                    sale_price = float(field.value.replace('₹', '').strip())
                except ValueError:
                    print(f"Could not parse sale_price from embed field: {field.value}")

        if embed.footer and "Product ID:" in embed.footer.text:
            match = re.search(r'Product ID:\s*([A-Z0-9]+)', embed.footer.text)
            if match:
                product_id = match.group(1)
        
        # Instantiate the view with the reconstructed data
        instance = cls(bot_instance, product_id, product_name, sale_price)

        # Check the button state from the message's components to infer if it was claimed
        if message.components:
            for component_row in message.components:
                for button in component_row.children:
                    if button.custom_id == "flash_sale_buy_now" and button.disabled:
                        instance.claimed = True
                        instance.children[0].disabled = True
                        instance.children[0].label = "Claimed!"
                        break
                if instance.claimed:
                    break
        return instance


    @discord.ui.button(label="Buy Now!", style=discord.ButtonStyle.success, emoji="⚡", custom_id="flash_sale_buy_now")
    async def buy_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=False, thinking=True) # Defer publicly for visible feedback
        
        # In-memory check for first-come, first-served during this bot's current uptime.
        # If the bot restarts, this `self.claimed` state is reset.
        if self.claimed:
            await interaction.followup.send("Sorry, this flash sale has already been claimed!", ephemeral=True)
            return
        
        # For a truly persistent flash sale claim across bot restarts, you MUST save this state to a file.
        # Example (uncomment if you add 'active_flash_sale_claimed' to store_state.json):
        # store_state = await self.bot.load_json('store_state')
        # if store_state.get('active_flash_sale_claimed', False):
        #     await interaction.followup.send("Sorry, this flash sale has already been claimed!", ephemeral=True)
        #     return
        
        # Immediately set `self.claimed` to True and disable the button to prevent race conditions
        self.claimed = True
        self.winner_id = interaction.user.id # Store winner ID in memory
        button.disabled = True
        button.label = "Claimed!"
        await interaction.message.edit(view=self) # Update the message to disable the button

        # Announce the winner publicly
        await interaction.followup.send(f"🎉 Congratulations {interaction.user.mention}, you claimed the flash sale for **{self.product_name}**!", ephemeral=False)

        # Create the order for the winner
        orders = await self.bot.load_json('orders') # Load orders from JSON
        counters = await self.bot.load_json('counters') # Load counters for new order ID
        
        last_order_number = counters.get('last_order_number', 0)
        new_order_number = last_order_number + 1
        order_id = f"ORD{new_order_number:04d}" # Generate unique order ID
        counters['last_order_number'] = new_order_number
        await self.bot.save_json('counters', counters) # Save updated counters

        products_db = await self.bot.load_json('products') # Load products data for original price lookup
        product_data = products_db.get(self.product_id)
        
        if not product_data:
            await interaction.user.send(f"⚠️ An error occurred: The product `{self.product_id}` was not found in the store. Please contact staff to manually process your flash sale win for **{self.product_name}**.")
            print(f"Flash sale product {self.product_id} not found when creating order.")
            return

        # Store the flash sale order details
        orders[order_id] = {
            "user_id": interaction.user.id,
            "items": { self.product_id: {"name": self.product_name, "price": self.sale_price, "quantity": 1} },
            "status": "Pending Payment", # Status for staff to process payment
            "discount": 0.0, # Flash sales are inherently discounted, no additional discount
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "channel_id": None, # No specific ticket channel initially for this direct order
            "notes": f"Won via Flash Sale. Original Price: ₹{product_data.get('price', 'N/A')}"
        }
        await self.bot.save_json('orders', orders) # Save the new order

        # For true persistence, update store_state to permanently mark this specific sale as claimed:
        # store_state['active_flash_sale_claimed'] = True
        # store_state['flash_sale_winner_id'] = interaction.user.id
        # await self.bot.save_json('store_state', store_state)

        # DM the winner to complete their purchase
        try:
            embed = discord.Embed(
                title="⚡ Your Flash Sale Order!",
                description=f"You've successfully claimed the flash sale for **{self.product_name}** at an incredible **₹{self.sale_price:.2f}**!\n"
                            f"Your new Order ID is `#{order_id}`.",
                color=int(self.bot.config['success_color'], 16),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.add_field(
                name="Next Steps", 
                value=f"Please open a 'Buy Products/Services' ticket in the server and provide this Order ID (`#{order_id}`) to a staff member. Mention it's a flash sale order to expedite processing and payment.", 
                inline=False
            )
            embed.set_footer(text="This special price is only valid for this order and is a one-time offer. Act fast!")
            await interaction.user.send(embed=embed)
            print(f"DM sent to flash sale winner {interaction.user.id} for order {order_id}.")
        except discord.Forbidden:
            await interaction.channel.send(f"⚠️ {interaction.user.mention}, I couldn't DM you your flash sale order details. Please open a ticket and reference order `#{order_id}` to complete your purchase.", ephemeral=False)
            print(f"Could not send flash sale DM to {interaction.user.id}: DMs disabled.")
        except Exception as e:
            await interaction.channel.send(f"❌ An error occurred while trying to DM your flash sale order. Please open a ticket and reference order `#{order_id}` to complete your purchase.", ephemeral=False)
            print(f"Error sending flash sale DM to winner {interaction.user.id}: {type(e).__name__}: {e}")

# --- Main Marketing Cog ---
class Marketing(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Start the scheduled tasks check only if it's not already running
        if not self.check_scheduled_tasks.is_running():
            self.check_scheduled_tasks.start()
            print("Scheduled tasks checker started for Marketing cog.")

    def cog_unload(self):
        # Cancel the task when the cog is unloaded to prevent errors
        self.check_scheduled_tasks.cancel()
        print("Scheduled tasks checker cancelled for Marketing cog.")

    # --- AFFILIATE / REFERRAL PROGRAM ---
    @app_commands.command(name="referral", description="Generate your personal referral code to invite friends.")
    async def referral(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        referrals = await self.bot.load_json('referrals') # Load referral codes data
        user_id_str = str(interaction.user.id) # Convert user ID to string for JSON key consistency
        
        # Check if the user already has an active referral code
        existing_code = None
        for code, referrer_id in referrals.items():
            if str(referrer_id) == user_id_str: # Compare string representations
                existing_code = code
                break
        
        if existing_code:
            await interaction.followup.send(f"You already have an active referral code: `{existing_code}`. Share this with new users to earn rewards!", ephemeral=True)
            return

        counters = await self.bot.load_json('counters') # Load counters for unique referral ID
        last_referral_number = counters.get('last_referral_number', 0)
        new_referral_number = last_referral_number + 1
        
        # Generate a unique code (e.g., REF-001, REF-002, REF-ABCDE)
        # Using a combination of a prefix and a sequential number
        code = f"REF-{new_referral_number:04d}" # Example: REF-0001
        
        referrals[code] = interaction.user.id # Store the user's ID as the referrer for this code
        counters['last_referral_number'] = new_referral_number
        
        await self.bot.save_json('referrals', referrals) # Save updated referral codes
        await self.bot.save_json('counters', counters) # Save updated counters

        affiliate_config = self.bot.config.get('loyalty_program', {}).get('affiliate_program', {})
        new_user_discount = affiliate_config.get('new_user_discount_inr', 0.0)
        referrer_reward = affiliate_config.get('referrer_reward_discount_inr', 0.0)

        embed = discord.Embed(
            title="🤝 Your Personal Referral Code",
            description=f"Share this unique code with new customers! When they use it during their first purchase, they receive a discount, and you earn a reward.",
            color=int(self.bot.config['embed_color'], 16),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="Your Code", value=f"`{code}`", inline=False)
        embed.add_field(
            name="How it Works", 
            value=f"- New users get **₹{new_user_discount:.2f}** off their first order.\n- You get a **₹{referrer_reward:.2f}** discount code when they complete their first purchase!", 
            inline=False
        )
        embed.set_footer(text="This code is only valid for new customers without prior delivered orders.")
        
        await interaction.followup.send(embed=embed, ephemeral=True)


    # --- FEATURED PRODUCT / DEAL OF THE DAY ---
    @app_commands.command(name="feature_product", description="[OWNER] Set a product as the 'Deal of the Day'.")
    @is_owner()
    # Reference the global helper function for autocomplete
    @app_commands.autocomplete(product_id=product_autocomplete_from_product_management)
    async def feature_product(self, interaction: discord.Interaction, product_id: str):
        await interaction.response.defer(ephemeral=True)
        products = await self.bot.load_json('products') # Load products data
        product_id = product_id.upper() # Ensure consistent casing
        
        if product_id not in products:
            await interaction.followup.send("❌ Product ID not found. Please use a valid product ID from `/browse`.", ephemeral=True)
            return
            
        store_state = await self.bot.load_json('store_state') # Load store state data
        store_state['featured_product_id'] = product_id # Store the featured product ID
        await self.bot.save_json('store_state', store_state) # Save updated store state

        # Get product name for confirmation message
        product_name = products[product_id].get('name', 'Unnamed Product')
        await interaction.followup.send(f"✅ **{product_name}** (ID: `{product_id}`) is now the featured product (Deal of the Day)!", ephemeral=True)

    @app_commands.command(name="deal", description="Check out the current featured product or deal of the day.")
    async def deal(self, interaction: discord.Interaction):
        await interaction.response.defer() # Defer publicly for visible response
        store_state = await self.bot.load_json('store_state') # Load store state data
        featured_id = store_state.get('featured_product_id')
        
        if not featured_id:
            await interaction.followup.send("There is no featured deal right now. Check back later for exciting offers!")
            return
            
        products = await self.bot.load_json('products') # Load products data
        product = products.get(featured_id)
        if not product:
            await interaction.followup.send("The featured product is no longer available in our catalog. It may have been removed or its ID changed. Please check `/browse` for available products.")
            # Clean up stale featured_id if product is gone
            store_state.pop('featured_product_id', None)
            await self.bot.save_json('store_state', store_state)
            return

        embed = discord.Embed(
            title=f"🔥 Deal of the Day: {product.get('name', 'Unnamed Product')}",
            description=product.get('description', 'No description provided for this product.'),
            color=discord.Color.gold(), # Using a distinct color for deals
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        if image_url := product.get('image_url'):
            embed.set_thumbnail(url=image_url) # Use image_url from product data if available
        
        price_display = f"**₹{product.get('price', 0.0):.2f}**" if product.get('price') is not None else "**Custom Quote**"
        embed.add_field(name="Price", value=price_display, inline=True)
        
        stock_num = product.get('stock', 0)
        if stock_num == -1: 
            stock_status = "Available (Infinite Stock)"
        elif stock_num > 0: 
            stock_status = f"{stock_num} in stock"
        else: 
            stock_status = "Out of Stock 🚫"
        embed.add_field(name="Stock", value=stock_status, inline=True)

        embed.set_footer(text=f"Product ID: {featured_id} | Open a ticket with the Buy button to purchase this item!")
        await interaction.followup.send(embed=embed)

    # --- FLASH SALE ---
    @app_commands.command(name="start_flash_sale", description="[OWNER] Start a first-come, first-served flash sale.")
    @is_owner()
    # Reference the global helper function for autocomplete
    @app_commands.autocomplete(product_id=product_autocomplete_from_product_management)
    async def start_flash_sale(self, interaction: discord.Interaction, product_id: str, sale_price: float, channel: discord.TextChannel):
        await interaction.response.defer(ephemeral=True) # Defer ephemerally for the staff user
        products = await self.bot.load_json('products') # Load products data
        product_id = product_id.upper() # Ensure consistent casing
        product = products.get(product_id)
        if not product:
            await interaction.followup.send("❌ Product ID not found. Please select a valid product for the flash sale.", ephemeral=True)
            return

        if sale_price <= 0:
            await interaction.followup.send("❌ Sale price must be a positive number.", ephemeral=True)
            return
        if product.get('price') is not None and sale_price >= product['price']:
            await interaction.followup.send(f"❌ Sale price (₹{sale_price:.2f}) must be strictly lower than the original price (₹{product['price']:.2f}).", ephemeral=True)
            return

        # Check if there's an active flash sale already (this is in-memory for current bot uptime)
        # For true persistence across bot restarts, you'd need to save/load this in store_state.json
        # e.g., check `store_state.get('active_flash_sale_message_id')`
        
        embed = discord.Embed(
            title="⚡ FLASH SALE! ⚡",
            description=f"Be the first to click the button and claim **{product.get('name', 'Unnamed Product')}** for an incredible price!",
            color=discord.Color.red(), # Red for urgency
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        if image_url := product.get('image_url'):
            embed.set_thumbnail(url=image_url) # Use product's image
        
        embed.add_field(name="Product", value=product.get('name', 'N/A'), inline=True)
        
        # Display original price if available
        if product.get('price') is not None:
            embed.add_field(name="Original Price", value=f"~~₹{product['price']:.2f}~~", inline=True)
        else:
            embed.add_field(name="Original Price", value="Not specified", inline=True)
            
        embed.add_field(name="SALE PRICE", value=f"**₹{sale_price:.2f}**", inline=True)
        embed.set_footer(text=f"Act fast! Only one available. Product ID: {product_id}") # Add product ID to footer for persistence recovery

        try:
            # Instantiate view with current values for the flash sale
            view = FlashSaleView(self.bot, product_id, product.get('name', 'Unnamed Product'), sale_price)
            
            # Send the flash sale announcement message to the specified channel
            message = await channel.send(content="@here", embed=embed, view=view) # Pings @here
            
            # If you want true persistence for the flash sale across restarts (to prevent re-claiming),
            # you would save `message.id`, `channel.id`, `product_id`, `sale_price`, and a `claimed: False` flag
            # in your `store_state.json` file here. Then on bot start, load that state and disable the view.
            # Example (uncomment if you add this logic):
            # store_state = await self.bot.load_json('store_state')
            # store_state['active_flash_sale'] = {
            #     'message_id': message.id,
            #     'channel_id': channel.id,
            #     'product_id': product_id,
            #     'sale_price': sale_price,
            #     'claimed_by_id': None # Null initially
            # }
            # await self.bot.save_json('store_state', store_state)

            await interaction.followup.send(f"✅ Flash sale for **{product.get('name', 'Unnamed Product')}** has been started in {channel.mention}. It will automatically be claimed by the first user.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(f"❌ I don't have permission to send messages in {channel.mention}. Please check my permissions and try again.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ An unexpected error occurred while starting the flash sale: {type(e).__name__}: {e}", ephemeral=True)
            print(f"Error starting flash sale: {type(e).__name__}: {e}")

    # --- SCHEDULED ANNOUNCEMENTS ---
    @app_commands.command(name="schedule_announcement", description="[OWNER] Schedule a message to be sent later.")
    @is_owner()
    @app_commands.describe(
        channel="The channel where the announcement will be sent.",
        message="The content of the announcement message.",
        send_in="Time until sending (e.g., '1h 30m', '3d', '15s'). Maximum 30 days."
    )
    async def schedule_announcement(self, interaction: discord.Interaction, channel: discord.TextChannel, message: str, send_in: str):
        await interaction.response.defer(ephemeral=True)
        
        seconds = 0
        time_unit_map = {'d': 86400, 'h': 3600, 'm': 60, 's': 1}
        
        # Validate time format (e.g., "1d 2h 30m")
        # Ensure it starts with digits and a unit, optionally followed by space and more units
        if not re.fullmatch(r'(\d+[dhms]\s*)+', send_in.lower().strip()):
             await interaction.followup.send("❌ Invalid time format. Please use units like `d` (days), `h` (hours), `m` (minutes), `s` (seconds) (e.g., `1h 30m`, `3d`).", ephemeral=True)
             return

        # Parse time components
        for match in re.finditer(r'(\d+)([dhms])', send_in.lower()):
            value, unit = int(match.group(1)), match.group(2)
            seconds += value * time_unit_map[unit]

        if seconds == 0:
            await interaction.followup.send("❌ The delay must be greater than zero. Please use a valid time (e.g., `1m` for 1 minute).", ephemeral=True)
            return
        
        if seconds > 30 * 86400: # Max 30 days for example
            await interaction.followup.send("❌ You can only schedule announcements up to 30 days in advance.", ephemeral=True)
            return

        due_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
        tasks = await self.bot.load_json('scheduled_tasks') # Load scheduled tasks from JSON
        if not isinstance(tasks, list): 
            print("Warning: 'scheduled_tasks.json' is not a list. Initializing as empty list.")
            tasks = [] # Ensure it's a list if corrupted

        task_id = secrets.token_hex(4) # Generate a unique ID for the task
        tasks.append({
            "task_id": task_id,
            "due_at": due_at.isoformat(), # Store datetime in ISO format for easy parsing
            "channel_id": channel.id,
            "message": message
        })
        await self.bot.save_json('scheduled_tasks', tasks) # Save updated scheduled tasks

        await interaction.followup.send(f"✅ Announcement scheduled successfully! It will be sent to {channel.mention} at <t:{int(due_at.timestamp())}:F> (Discord's full timestamp format).", ephemeral=True)

    @tasks.loop(seconds=60.0) # This task runs every 60 seconds (1 minute)
    async def check_scheduled_tasks(self):
        # Wait until the bot is fully ready and has loaded all cogs and data
        await self.bot.wait_until_ready() 
        
        # Prevents the task from running before commands are synced on bot startup
        if not self.bot.synced: 
            return 
            
        # print(f"Running scheduled tasks check at {datetime.datetime.now(datetime.timezone.utc).isoformat()}...") # Uncomment for detailed logging
        
        tasks = await self.bot.load_json('scheduled_tasks') # Load scheduled tasks from JSON
        if not isinstance(tasks, list): 
            print("Warning: 'scheduled_tasks.json' is not a list. Resetting to empty list for safety.")
            tasks = []
            await self.bot.save_json('scheduled_tasks', tasks)
            return

        now = datetime.datetime.now(datetime.timezone.utc)
        remaining_tasks = [] # List to store tasks that are not yet due or failed to send
        tasks_executed_count = 0

        for task in tasks:
            try:
                due_at = datetime.datetime.fromisoformat(task['due_at'])
                if now >= due_at: # Check if the task is due
                    channel_id = task.get('channel_id')
                    message_content = task.get('message')

                    if not channel_id or not message_content:
                        print(f"Skipping scheduled task {task.get('task_id', 'unknown')}: Missing channel ID or message content. Task removed.")
                        continue # Skip malformed tasks, they will not be added to remaining_tasks

                    channel = self.bot.get_channel(channel_id)
                    if channel:
                        if isinstance(channel, discord.TextChannel): # Ensure it's a text channel before sending
                            embed = discord.Embed(
                                description=message_content, 
                                color=int(self.bot.config['embed_color'], 16),
                                timestamp=datetime.datetime.now(datetime.timezone.utc) # Add timestamp to embed
                            )
                            embed.set_footer(text="Scheduled Announcement")
                            try:
                                await channel.send(embed=embed)
                                tasks_executed_count += 1
                                print(f"Sent scheduled task {task.get('task_id', 'unknown')} to {channel.name}.")
                            except discord.Forbidden:
                                print(f"Failed to send scheduled task {task.get('task_id', 'unknown')} to channel {channel.id}: Missing permissions. Task removed.")
                            except Exception as e:
                                print(f"Error sending scheduled task {task.get('task_id', 'unknown')} to channel {channel.id}: {type(e).__name__}: {e}. Task removed.")
                        else:
                            print(f"Channel (ID: {channel_id}) for task {task.get('task_id', 'unknown')} is not a text channel. Task removed.")
                    else:
                        print(f"Channel (ID: {channel_id}) for task {task.get('task_id', 'unknown')} not found. Task removed.")
                else:
                    remaining_tasks.append(task) # Task is not yet due, keep it
            except ValueError:
                print(f"Malformed 'due_at' timestamp for task {task.get('task_id', 'unknown')}. Task removed.")
            except Exception as e:
                print(f"An unexpected error occurred processing scheduled task {task.get('task_id', 'unknown')}: {type(e).__name__}: {e}. Task removed.")
        
        # Only save if there were changes to the tasks list (tasks executed or errors removed them)
        if len(remaining_tasks) != len(tasks) or tasks_executed_count > 0:
            await self.bot.save_json('scheduled_tasks', remaining_tasks)
            if tasks_executed_count > 0:
                print(f"Executed {tasks_executed_count} scheduled tasks during this check.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Marketing(bot))
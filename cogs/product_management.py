# cogs/product_management.py
import discord
from discord.ext import commands
from discord import app_commands
import uuid
import re
import datetime
from utils.checks import is_owner

# A forward import is needed for type hinting without circular import errors
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    # These imports are only for type checking, not loaded at runtime
    from cogs.ticket_system import ShoppingCartView, StaffTicketView


class ProductModal(discord.ui.Modal, title="Product Details"):
    def __init__(self, bot, product_id=None, existing_product=None):
        super().__init__()
        self.bot = bot
        self.product_id = product_id or str(uuid.uuid4())[:6].upper() # Generate a new ID if not provided
        
        # --- TextInputs with explicit row assignments ---
        # Discord Modals support a maximum of 5 TextInput components. Each TextInput occupies a full row.
        # So, we must assign each TextInput to its own unique row from 0 to 4.
        self.name_input = discord.ui.TextInput( # Renamed to avoid clash with method if any
            label="Product Name", 
            default=existing_product.get('name', '') if existing_product else '',
            required=True,
            row=0 
        )
        self.description_input = discord.ui.TextInput( # Renamed
            label="Description", 
            style=discord.TextStyle.paragraph, 
            default=existing_product.get('description', '') if existing_product else '',
            required=True,
            row=1 
        )
        self.price_input = discord.ui.TextInput( # Renamed
            label="Price (INR)", 
            required=True, 
            placeholder="e.g., 100.00", 
            default=str(existing_product.get('price', '')) if existing_product and existing_product.get('price') is not None else '',
            row=2 
        )
        self.stock_input = discord.ui.TextInput( # Renamed
            label="Stock Quantity (-1 for infinite)", 
            default=str(existing_product.get('stock', -1)) if existing_product and existing_product.get('stock') is not None else '-1',
            required=True,
            row=3 
        )
        
        # The 5th and final TextInput for the modal.
        self.renewal_period_days_input = discord.ui.TextInput( # Renamed
            label="Renewal Period in Days (Optional)", 
            required=False, 
            placeholder="e.g., 30 for a monthly subscription", 
            default=str(existing_product.get('renewal_period_days', '')) if existing_product and existing_product.get('renewal_period_days') is not None else '',
            row=4 
        )

        # Removed emoji and image_url TextInputs from the modal as it can only have 5.
        # These fields will retain their existing values if editing, or be None for new products.
        # They can be updated via separate commands or parameters on /edit_product if implemented.

        # Add all 5 TextInputs to the modal
        self.add_item(self.name_input)
        self.add_item(self.description_input)
        self.add_item(self.price_input)
        self.add_item(self.stock_input)
        self.add_item(self.renewal_period_days_input)


    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True) # Defer immediately for modal submission
        products = await self.bot.load_json('products') # Using bot's load_json
        
        price_val = None
        stock_val = -1 # Default to infinite stock
        renewal_val = None
        
        # Validate and convert price
        if self.price_input.value.strip(): # Check if input is not empty or just whitespace
            try:
                price_val = float(self.price_input.value.strip())
                if price_val < 0: 
                    raise ValueError("Price cannot be negative.")
            except ValueError as e:
                await interaction.followup.send(f"❌ Invalid price format: {e}. Please enter a valid positive number for price (e.g., 100.00).", ephemeral=True)
                return
        else: # If price is required but not provided
            await interaction.followup.send("❌ Product price is required. Please enter a numerical value.", ephemeral=True)
            return

        # Validate and convert stock
        if self.stock_input.value.strip(): # Check if input is not empty or just whitespace
            try:
                stock_val = int(self.stock_input.value.strip())
                if stock_val < -1: 
                    raise ValueError("Stock cannot be less than -1 (use -1 for infinite).")
            except ValueError as e:
                await interaction.followup.send(f"❌ Invalid stock quantity format: {e}. Please enter an integer (-1 for infinite).", ephemeral=True)
                return
        else: # If stock is required but not provided
            await interaction.followup.send("❌ Stock quantity is required. Please enter an integer.", ephemeral=True)
            return

        # Validate and convert renewal period (optional)
        if self.renewal_period_days_input.value.strip():
            try:
                renewal_val = int(self.renewal_period_days_input.value.strip())
                if renewal_val <= 0: 
                    raise ValueError("Renewal period must be a positive integer in days.")
            except ValueError as e:
                await interaction.followup.send(f"❌ Invalid renewal period format: {e}. Please enter a positive number of days (e.g., 30).", ephemeral=True)
                return
            
        # Get old product data to preserve existing optional fields like emoji and image_url
        old_product_data = products.get(self.product_id, {})
        old_stock = old_product_data.get('stock', 0) # For restock notification logic
        
        # Create or update product data. Preserve emoji and image_url.
        products[self.product_id] = {
            "name": self.name_input.value.strip(), # Ensure name is stripped of whitespace
            "description": self.description_input.value.strip(), # Ensure description is stripped
            "price": price_val,
            "stock": stock_val,
            # Retain existing emoji and image_url. They are not updated via this modal.
            "emoji": old_product_data.get('emoji', None), 
            "image_url": old_product_data.get('image_url', None), 
            "renewal_period_days": renewal_val 
        }
        
        await self.bot.save_json('products', products) # Save updated products data
        
        # Restock notification logic: if product was out of stock and is now in stock
        if old_stock == 0 and stock_val > 0: 
            notifications = await self.bot.load_json('notifications') # Load notification data
            user_ids_to_notify = notifications.pop(self.product_id, []) # Get and remove entries for this product
            
            if user_ids_to_notify:
                notification_count = 0
                for user_id in user_ids_to_notify:
                    try:
                        user = await self.bot.fetch_user(user_id)
                        await user.send(f"🎉 **Restock Alert!**\nThe product '{self.name_input.value.strip()}' is now back in stock! Check it out in the store: `/browse`")
                        notification_count += 1
                    except discord.Forbidden: # User has DMs disabled
                        print(f"Failed to send restock notification to user {user_id}: DMs disabled.")
                    except Exception as e:
                        print(f"Failed to send restock notification to user {user_id}: {type(e).__name__}: {e}")
                await self.bot.save_json('notifications', notifications) # Save updated notifications (without sent entries)
                
                await interaction.followup.send(f"✅ Product `{self.name_input.value.strip()}` (ID: `{self.product_id}`) saved. Sent {notification_count} restock alerts to interested users.", ephemeral=True)
                return # Exit early if restock alert was sent

        await interaction.followup.send(f"✅ Product `{self.name_input.value.strip()}` (ID: `{self.product_id}`) has been successfully saved/updated.", ephemeral=True)


class QuickAddModal(discord.ui.Modal, title="Quick Add to Cart"):
    def __init__(self, bot, page_products: list):
        super().__init__()
        self.bot = bot
        
        # Dynamically generate placeholder with actual product IDs from the page
        placeholder_text = "No products on this page"
        if page_products:
            sample_pids = [p[0] for p in page_products[:3]] # Take up to 3 product IDs for example
            placeholder_text = "e.g., " + ", ".join(sample_pids)
        
        self.product_id_input = discord.ui.TextInput(
            label="Enter the Product ID from this page", 
            placeholder=placeholder_text,
            required=True
        )
        self.add_item(self.product_id_input)

    async def on_submit(self, interaction: discord.Interaction):
        product_id = self.product_id_input.value.strip().upper()
        
        # Get the ProductManagement cog instance
        product_cog = self.bot.get_cog("ProductManagement")
        if product_cog:
            await interaction.response.defer(ephemeral=True) # Defer immediately for modal submission
            # Re-uses the logic from the /quick_buy command within the cog
            await product_cog.execute_quick_buy(interaction, product_id)
        else:
            await interaction.response.send_message("❌ Product management system is currently unavailable. Please notify a bot administrator.", ephemeral=True)


class ProductBrowserView(discord.ui.View):
    def __init__(self, bot, products: dict):
        super().__init__(timeout=180) # View times out after 3 minutes of inactivity
        self.bot = bot
        # Convert dictionary items to a list of (id, data) tuples for easy pagination
        self.products = list(products.items()) 
        self.current_page = 0
        self.items_per_page = 3 # Number of products to display per page

    async def get_page_embed(self):
        start_index = self.current_page * self.items_per_page
        end_index = start_index + self.items_per_page
        page_products = self.products[start_index:end_index] # Get products for the current page
        
        embed = discord.Embed(
            title="🛍️ Our Products", 
            description="Browse through our catalog using the navigation buttons. Click 'Add to Cart' to start a quick purchase.", 
            color=int(self.bot.config['embed_color'], 16)
        )
        
        # Set thumbnail using the image_url of the first product on the page, if available
        if page_products and page_products[0][1].get("image_url"):
            embed.set_thumbnail(url=page_products[0][1]["image_url"])
            
        if not page_products:
            embed.description = "There are no products to display on this page or in the store currently."
        
        for pid, p in page_products:
            # Ensure price is formatted correctly, handle None price
            price = f"₹{p.get('price', 0.0):.2f}" if p.get('price') is not None else "Custom Quote" 
            emoji = p.get('emoji', '📦') # Use custom emoji or a default box emoji
            
            try: # Ensure stock is int before using
                stock_num = int(p.get('stock', 0)) 
            except ValueError:
                stock_num = 0 # Default to 0 if malformed
                print(f"Warning: Product {pid} has non-integer stock '{p.get('stock')}'. Defaulting to 0 for display.")

            if stock_num == -1: 
                stock_status = "Available (Infinite Stock)"
            elif stock_num > 0: 
                stock_status = f"{stock_num} in stock"
            else: 
                stock_status = "Out of Stock 🚫" # Clearly indicate out of stock
            
            # Add renewal period information if available
            renewal_info = ""
            if p.get('renewal_period_days'):
                renewal_info = f"\n> **Renews Every:** {p['renewal_period_days']} days"

            # Use product.get('description') in the field value itself
            product_description = p.get('description', 'No description provided.').strip()
            if product_description:
                product_description = f"> **Description:** {product_description}\n"
            else:
                product_description = "" # No description line if empty
                
            field_value = (
                f"> **Price:** {price}\n"
                f"> **Stock:** {stock_status}"
                f"{renewal_info}\n"
                f"{product_description}" # Add description here
            ).strip() # Remove trailing newlines/spaces

            # Check if name is None or empty and provide a fallback.
            product_name_display = p.get('name')
            if not product_name_display:
                product_name_display = "Unnamed Product"
            
            embed.add_field(
                name=f"{emoji} {product_name_display} (ID: `{pid}`)", # Handle missing product name
                value=field_value, 
                inline=False # Each product takes its own line
            )
            
        total_pages = -(-len(self.products) // self.items_per_page) # Ceiling division for total pages
        embed.set_footer(text=f"Page {self.current_page + 1} / {total_pages}")
        return embed

    async def update_view(self, interaction: discord.Interaction):
        """Updates the embed and button states of the current browser view."""
        embed = await self.get_page_embed()
        # Disable/enable buttons based on current page
        self.children[0].disabled = self.current_page == 0 # Previous button
        # Next button disabled if current page is the last page (or there are no products)
        self.children[1].disabled = (self.current_page + 1) * self.items_per_page >= len(self.products)
        
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, emoji="◀️", row=0, custom_id="browser_previous")
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page -= 1
        await self.update_view(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, emoji="▶️", row=0, custom_id="browser_next")
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.current_page += 1
        await self.update_view(interaction)

    @discord.ui.button(label="Add to Cart", style=discord.ButtonStyle.success, emoji="🛒", row=1, custom_id="browser_add_to_cart")
    async def quick_add_to_cart(self, interaction: discord.Interaction, button: discord.ui.Button):
        start_index = self.current_page * self.items_per_page
        end_index = start_index + self.items_per_page
        page_products = self.products[start_index:end_index]

        # Only send modal if there are products on the current page to select from
        if not page_products:
            await interaction.response.send_message("There are no products on this page to add to cart. Please navigate to a page with products.", ephemeral=True)
            return

        # Send the QuickAddModal to get product ID from user
        await interaction.response.send_modal(QuickAddModal(self.bot, page_products))


class ProductManagement(commands.Cog):
    def __init__(self, bot: commands.Bot): 
        self.bot = bot
        # Initialize active_tickets in bot if not already present. This is a shared state.
        if not hasattr(bot, 'active_tickets'):
            self.bot.active_tickets = {}

    async def product_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """Autocomplete function for product IDs based on name or ID."""
        products = await self.bot.load_json('products') # Load products from JSON
        choices = []
        for pid, product in products.items():
            product_name = product.get('name', 'Unnamed Product')
            # Check if current input matches product name or ID (case-insensitive)
            if current.lower() in product_name.lower() or current.lower() in pid.lower():
                choices.append(app_commands.Choice(name=f"{product_name} (ID: {pid})", value=pid)) # More descriptive label
        return choices[:25] # Discord API limit is 25 choices

    async def execute_quick_buy(self, interaction: discord.Interaction, product_id: str):
        """
        Helper function to handle the quick buy process (creating a ticket and adding a product).
        This is used by both the /quick_buy command and the "Add to Cart" button in /browse.
        """
        # This interaction might come from a modal, so we need a fresh deferral if not already deferred.
        if not interaction.response.is_done():
            # Change this from ephemeral=True to ephemeral=False
            await interaction.response.defer(ephemeral=False) # <--- THIS WAS CHANGED

        products = await self.bot.load_json('products') # Load current products data
        product_id = product_id.upper() # Ensure consistent casing
        product = products.get(product_id)
        
        if not product:
            await interaction.followup.send("❌ Product ID not found. Please ensure you entered a valid ID.", ephemeral=True)
            return
        
        stock = product.get('stock', 0) # Get stock, default to 0
        if stock == 0:
            await interaction.followup.send("❌ This product is currently out of stock. You cannot quick buy it. Use `/notify_me` to get an alert when it's back in stock!", ephemeral=True)
            return

        ticket_cog = self.bot.get_cog("TicketSystem")
        if not ticket_cog:
            await interaction.followup.send("Ticket system is currently unavailable. Please notify a bot administrator.", ephemeral=True)
            return
            
        # Get the ticket panel channel from config for creating the thread
        panel_channel_id = self.bot.config.get("ticket_panel_channel_id")
        panel_channel = self.bot.get_channel(panel_channel_id)
        
        if not panel_channel or not isinstance(panel_channel, discord.TextChannel):
            await interaction.followup.send("❌ Ticket panel channel is not configured correctly by the owner, or I cannot access it. Cannot create ticket.", ephemeral=True)
            return

        try:
            # Dynamically import ShoppingCartView and StaffTicketView here to avoid circular imports at file load time
            from cogs.ticket_system import ShoppingCartView, StaffTicketView
            
            # Construct a dynamic thread name (max 100 characters)
            product_name_for_thread = product.get('name', 'Product') # Default if name is missing
            sanitized_name = re.sub(r'[^a-zA-Z0-9-]', '', product_name_for_thread).strip()
            if not sanitized_name: sanitized_name = "item" # Fallback if sanitized name is empty
            
            base_thread_name = f"🛒-{interaction.user.name}"
            # Ensure the combined length doesn't exceed 100 characters for the thread name
            remaining_len = 100 - len(base_thread_name) - 1 # For dash
            final_thread_name = f"{base_thread_name}-{sanitized_name[:remaining_len]}"
            final_thread_name = final_thread_name[:100].strip('- ') # Final truncation and cleanup
            
            thread = await panel_channel.create_thread(
                name=final_thread_name, 
                type=discord.ChannelType.private_thread, # Create a private thread for tickets
                reason=f"Quick Buy ticket by {interaction.user.name} for {product_name_for_thread}"
            )
            
            await interaction.followup.send(f"✅ Your quick buy ticket has been created: {thread.mention}", ephemeral=True)
            await thread.add_user(interaction.user) # Add the user to the private thread
            
            # Initialize cart with the quick-bought product
            # Ensure price is handled if None
            initial_price = product.get('price', 0.0)
            cart = {product_id: {"name": product.get('name', 'Unnamed Product'), "price": initial_price, "quantity": 1}}
            
            # Store ticket state in bot.active_tickets in memory
            self.bot.active_tickets[thread.id] = {
                "cart": cart, 
                "discount": 0.0, 
                "creator_id": interaction.user.id,
                "category": "BUY", # Explicitly set category
                "status": "Open",
                "cart_message_id": None # Will store the ID of the cart message
            }

            # Instantiate ShoppingCartView with products (needed for ProductSelect options)
            # It will load fresh products dynamically when needed.
            current_products_for_view = await self.bot.load_json('products')
            view = ShoppingCartView(self.bot, current_products_for_view)
            
            # Prepare initial cart embed for the ticket
            total = initial_price # Total for a single item
            description = f"**{product.get('name', 'Unnamed Product')}** x1 - `₹{total:.2f}`"
            embed = discord.Embed(
                title="🛒 Your Shopping Cart", 
                description=description, 
                color=int(self.bot.config['embed_color'], 16),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            embed.set_footer(text=f"Grand Total: ₹{total:.2f}")

            # Mentions for staff roles
            mentions = ' '.join([f'<@&{rid}>' for rid in self.bot.config.get('staff_role_ids', [])])
            
            # Send initial cart message in the thread
            cart_message = await thread.send(content=f"Welcome, {interaction.user.mention}! Your item has been added to the cart.\n{mentions}", embed=embed, view=view)
            self.bot.active_tickets[thread.id]['cart_message_id'] = cart_message.id # Store message ID for later updates
            
            # Send staff controls separately
            staff_control_embed = discord.Embed(description="--- **Staff Controls** ---", color=int(self.bot.config['embed_color'], 16))
            await thread.send(embed=staff_control_embed, view=StaffTicketView(self.bot, ticket_creator=interaction.user))
        
        except discord.errors.Forbidden:
            await interaction.followup.send("❌ I don't have permissions to create private threads in the ticket panel channel. Please check my permissions and ensure I have 'Manage Threads' and 'Create Private Threads'.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ An unexpected error occurred while creating your quick buy ticket: {type(e).__name__}: {e}. Please contact a bot administrator.", ephemeral=True)
            print(f"Error in quick_buy/execute_quick_buy for user {interaction.user.id}: {type(e).__name__}: {e}")


    @app_commands.command(name="add_product", description="[OWNER] Add a new product to the store.")
    @is_owner()
    async def add_product(self, interaction: discord.Interaction):
        # The modal handles its own deferral and response.
        await interaction.response.send_modal(ProductModal(self.bot))

    @app_commands.command(name="edit_product", description="[OWNER] Edit an existing product.")
    @is_owner()
    @app_commands.autocomplete(product_id=product_autocomplete) # Autocomplete uses the cog's own method
    async def edit_product(self, interaction: discord.Interaction, product_id: str):
        products = await self.bot.load_json('products') # Load products data
        product_id = product_id.upper() # Ensure consistent casing
        if not products.get(product_id):
            await interaction.response.send_message("❌ Product ID not found. Please ensure you enter a valid product ID to edit.", ephemeral=True)
            return
        # The modal handles its own deferral and response.
        await interaction.response.send_modal(ProductModal(self.bot, product_id=product_id, existing_product=products.get(product_id)))

    # --- NEW COMMAND ADDED HERE ---
    @app_commands.command(name="set_product_emoji", description="[OWNER] Set or update the emoji for a product.")
    @is_owner()
    @app_commands.autocomplete(product_id=product_autocomplete)
    @app_commands.describe(
        product_id="The ID of the product to update.",
        emoji="The custom emoji for the product (e.g., <:name:id:>)."
    )
    async def set_product_emoji(self, interaction: discord.Interaction, product_id: str, emoji: str):
        await interaction.response.defer(ephemeral=True)

        # Validate the emoji format using regex. Allows for animated and non-animated emojis.
        if not re.match(r'^<a?:\w+:\d+>$', emoji.strip()):
            await interaction.followup.send(
                "❌ Invalid emoji format. Please provide a custom emoji in the correct format: `<:emoji_name:emoji_id:>` or `<a:emoji_name:emoji_id:>` for animated emojis.",
                ephemeral=True
            )
            return
            
        products = await self.bot.load_json('products')
        product_id = product_id.upper()

        if product_id not in products:
            await interaction.followup.send(f"❌ Product with ID `{product_id}` not found.", ephemeral=True)
            return
            
        # Update the emoji for the specified product
        products[product_id]['emoji'] = emoji.strip()
        await self.bot.save_json('products', products)
        
        product_name = products[product_id].get('name', 'Unnamed Product')
        await interaction.followup.send(f"✅ Successfully updated the emoji for `{product_name}` to {emoji.strip()}.", ephemeral=True)


    @app_commands.command(name="browse", description="Browse all available products in an interactive menu.")
    async def browse(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False) # Changed to public response
        products = await self.bot.load_json('products') # Load products data
        if not products:
            await interaction.followup.send("There are no products in the store yet. Please check back later!", ephemeral=False) # Changed to public response
            return

        view = ProductBrowserView(self.bot, products)
        embed = await view.get_page_embed()

        # Set initial button states for the first page
        view.previous_button.disabled = True
        if len(products) <= view.items_per_page:
            view.next_button.disabled = True # Disable next if all products fit on one page

        await interaction.followup.send(embed=embed, view=view, ephemeral=False) # Changed to public response

    @app_commands.command(name="quick_buy", description="Instantly create a ticket to buy a specific product.")
    @app_commands.autocomplete(product_id=product_autocomplete) # Autocomplete uses the cog's own method
    @app_commands.describe(product_id="Start typing the name or ID of the product you want to buy.")
    async def quick_buy(self, interaction: discord.Interaction, product_id: str):
        # The execute_quick_buy helper function handles its own deferral.
        await self.execute_quick_buy(interaction, product_id)

    @app_commands.command(name="shop_stats", description="View public statistics about the store.")
    async def shop_stats(self, interaction: discord.Interaction):
        await interaction.response.defer() # Defer publicly
        orders = await self.bot.load_json('orders') # Load orders data
        products = await self.bot.load_json('products') # Load products data
        
        # Count only 'Delivered' orders for statistics
        total_orders = len([o for o in orders.values() if o.get('status') == 'Delivered'])
        total_products_available = len(products)
        
        embed = discord.Embed(
            title="📊 Store Statistics", 
            color=int(self.bot.config['embed_color'], 16),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.add_field(name="Total Products Available", value=f"`{total_products_available}`", inline=True)
        embed.add_field(name="Total Orders Completed", value=f"`{total_orders}`", inline=True)
        embed.set_footer(text="Statistics based on successfully delivered orders.")
        
        await interaction.followup.send(embed=embed)
        
    @app_commands.command(name="notify_me", description="Get a DM when an out-of-stock product is available again.")
    @app_commands.autocomplete(product_id=product_autocomplete) # Autocomplete uses the cog's own method
    async def notify_me(self, interaction: discord.Interaction, product_id: str):
        await interaction.response.defer(ephemeral=True) # Defer ephemerally
        products = await self.bot.load_json('products') # Load products data
        product_id = product_id.upper() # Ensure consistent casing
        product = products.get(product_id)
        
        if not product:
            await interaction.followup.send("❌ That Product ID doesn't exist. Please select a valid product from `/browse`.", ephemeral=True); return
        
        stock = product.get('stock', 0) # Get stock, default to 0
        if stock != 0:
            await interaction.followup.send("✅ Good news! That product is already in stock. You can purchase it now!", ephemeral=True); return
        
        notifications = await self.bot.load_json('notifications') # Load notifications data
        if product_id not in notifications:
            notifications[product_id] = [] # Initialize list for this product if it doesn't exist
        
        if interaction.user.id in notifications[product_id]:
            await interaction.followup.send("👍 You're already on the notification list for this item. We'll let you know when it's back!", ephemeral=True); return
        
        notifications[product_id].append(interaction.user.id) # Add user to notification list
        await self.bot.save_json('notifications', notifications) # Save updated notifications
        
        await interaction.followup.send(f"✅ You're on the list! I'll DM you when '{product.get('name', 'Unnamed Product')}' is back in stock.", ephemeral=True)

    @app_commands.command(name="review", description="Leave a review for a product you purchased.")
    @app_commands.autocomplete(product_id=product_autocomplete) # Autocomplete uses the cog's own method
    @app_commands.choices(rating=[
        app_commands.Choice(name="⭐ (Terrible)", value=1), 
        app_commands.Choice(name="⭐⭐ (Bad)", value=2),
        app_commands.Choice(name="⭐⭐⭐ (Okay)", value=3), 
        app_commands.Choice(name="⭐⭐⭐⭐ (Good)", value=4),
        app_commands.Choice(name="⭐⭐⭐⭐⭐ (Excellent)", value=5),
    ])
    async def review(self, interaction: discord.Interaction, product_id: str, rating: app_commands.Choice[int], comment: str):
        await interaction.response.defer(ephemeral=True) # Defer ephemerally
        products = await self.bot.load_json('products') # Load products data
        product_id = product_id.upper() # Ensure consistent casing
        product = products.get(product_id)
        
        if not product:
            await interaction.followup.send("❌ That Product ID doesn't exist in our catalog.", ephemeral=True); return
        
        # Check if user has purchased this product (good practice for review authenticity)
        orders = await self.bot.load_json('orders') # Load orders data
        user_has_purchased = any(
            order.get('user_id') == interaction.user.id and 
            order.get('status') == 'Delivered' and # Only allow reviews for delivered orders
            product_id in order.get('items', {})
            for order in orders.values()
        )
        if not user_has_purchased:
            await interaction.followup.send("❌ You can only leave reviews for products you have successfully purchased and received. If this is an error, please contact staff.", ephemeral=True)
            return

        review_channel_id = self.bot.config.get("review_channel_id")
        if not review_channel_id:
            await interaction.followup.send("⚠️ The review system is not configured correctly (review channel ID is missing in config). Please contact staff.", ephemeral=True); return
        
        review_channel = self.bot.get_channel(review_channel_id)
        if not review_channel or not isinstance(review_channel, discord.TextChannel):
            await interaction.followup.send("⚠️ The configured review channel could not be found or is not a text channel. Please contact staff to fix this.", ephemeral=True); return
        
        stars = "⭐" * rating.value # Generate star string
        review_embed = discord.Embed(
            title=f"New Review for {product.get('name', 'Unnamed Product')}", 
            color=int(self.bot.config['embed_color'], 16), 
            timestamp=interaction.created_at
        )
        review_embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        review_embed.add_field(name="Rating", value=f"{stars} ({rating.value}/5)", inline=False)
        
        # Truncate comment if too long for Discord embed field limit (1024 characters)
        if len(comment) > 1000:
            comment = comment[:997] + "..."
        review_embed.add_field(name="Comment", value=f"```\n{comment}\n```", inline=False) # Use code block for multi-line comment
        review_embed.set_footer(text=f"Product ID: {product_id} | User ID: {interaction.user.id}")
        
        try:
            await review_channel.send(embed=review_embed)
            await interaction.followup.send("✅ Thank you for your review! It has been submitted successfully.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I don't have permission to send messages to the review channel. Please contact staff.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ An unexpected error occurred while submitting your review: {type(e).__name__}: {e}", ephemeral=True)
            print(f"Error submitting review to channel {review_channel_id}: {type(e).__name__}: {e}")


    @app_commands.command(name="profile", description="View your or another user's store profile.")
    async def profile(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer(ephemeral=True) # Defer ephemerally
        target_user = user or interaction.user # Target user is specified user or command invoker
        
        users_data = await self.bot.load_json('users') # Load users data
        orders_data = await self.bot.load_json('orders') # Load orders data
        
        # Get points for the target user, default to 0 if not found
        points = users_data.get(str(target_user.id), {}).get('points', 0)
        
        # Count delivered orders for the target user
        order_count = sum(1 for o in orders_data.values() if o.get('user_id') == target_user.id and o.get('status') == 'Delivered')
        
        embed = discord.Embed(
            title=f"🛍️ Store Profile: {target_user.display_name}", 
            color=int(self.bot.config['embed_color'], 16),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_author(name=target_user.display_name, icon_url=target_user.display_avatar.url)
        embed.set_thumbnail(url=target_user.display_avatar.url)
        
        embed.add_field(name="✨ Infinity Points", value=f"`{points}` points", inline=True)
        embed.add_field(name="✅ Completed Orders", value=f"`{order_count}` orders", inline=True)
        
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="myorders", description="View your personal order history.")
    async def myorders(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True) # Defer ephemerally
        orders = await self.bot.load_json('orders') # Load orders data
        
        # Filter orders for the current user
        user_orders = {oid: o for oid, o in orders.items() if o.get('user_id') == interaction.user.id}
        
        if not user_orders:
            await interaction.followup.send("You have no past orders recorded. Start shopping today!", ephemeral=True); return
        
        embed = discord.Embed(
            title="📜 Your Recent Order History", 
            description="Here are your most recent orders:", 
            color=int(self.bot.config['embed_color'], 16),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

        # Sort orders by timestamp (newest first) and show up to 10
        # Use .get('timestamp') with a fallback to a very old date string for safe sorting
        sorted_orders = sorted(user_orders.items(), key=lambda item: item[1].get('timestamp', '1970-01-01T00:00:00+00:00'), reverse=True)[:10] 
        
        for order_id, order in sorted_orders:
            # Get item names, default to 'Unknown Product' if missing
            items_str = ", ".join(item.get('name', 'Unknown Product') for item in order.get('items', {}).values())
            
            order_time_str = order.get('timestamp')
            order_date_display = "Date N/A"
            if order_time_str:
                try:
                    # Convert ISO format string to datetime object, then to Discord timestamp format
                    order_time = datetime.datetime.fromisoformat(order_time_str)
                    order_date_display = f"<t:{int(order_time.timestamp())}:D>" # Short date format
                except ValueError:
                    print(f"Warning: Malformed timestamp for order {order_id}: {order_time_str}. Displaying as 'Date N/A'.")
            
            embed.add_field(
                name=f"Order `#{order_id}` - {order_date_display}", 
                value=f"**Status:** `{order.get('status', 'Unknown')}`\n**Items:** {items_str}", 
                inline=False # Each order takes its own field
            )
        
        embed.set_footer(text="Showing up to 10 most recent orders.")
        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(ProductManagement(bot))
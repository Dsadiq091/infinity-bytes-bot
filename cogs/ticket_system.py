# cogs/ticket_system.py
import discord
from discord.ext import commands
import json
import asyncio
import os
import chat_exporter
import uuid
import datetime
from discord import app_commands
import re 

# Using the checks from utils
from utils.checks import is_staff_or_owner

# A forward import is needed for type hinting without circular import errors
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    # These imports are only for type checking, they are not loaded at runtime
    # to prevent circular dependencies. The actual classes are imported dynamically
    # when needed (e.g., in main.py for persistent views or within functions).
    pass # Placeholder if no specific class to import for type checking


# --- UI View for Transcript Instructions ---
class TranscriptInstructionsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None) # Persists across restarts
        self.custom_id = "transcript_instructions_view" # Custom ID for persistence
        # Pre-define the instructions for each platform
        self.instructions = {
            "pc_mac": (
                "**💻 How to View on PC / Mac**\n"
                "1. Click the file above to download it.\n"
                "2. Open your computer's 'Downloads' folder.\n"
                "3. Double-click the file to open it in your web browser."
            ),
            "android": (
                "**🤖 How to View on Android**\n"
                "1. Tap the download icon on the file.\n"
                "2. Open your phone's **'Files'** or **'My Files'** app.\n"
                "3. Go to the 'Downloads' folder and tap the transcript file.\n"
                "4. If prompted, choose a browser like **Chrome** to open it."
            ),
            "ios": (
                "**🍎 How to View on iPhone / iPad**\n"
                "1. Tap the file in the chat.\n"
                "2. Tap the **Share icon** (box with an arrow) in the top-right corner.\n"
                "3. Select **'Save to Files'** and choose a location.\n"
                "4. Open the **'Files'** app on your device and tap the saved transcript to view it."
            )
        }

    @discord.ui.select(
        custom_id="transcript_instructions_dropdown",
        placeholder="Choose your device to see how to view the transcript...",
        options=[
            discord.SelectOption(label="PC / Mac", value="pc_mac", emoji="💻"),
            discord.SelectOption(label="Android", value="android", emoji="🤖"),
            discord.SelectOption(label="iOS (iPhone/iPad)", value="ios", emoji="🍎"),
        ]
    )
    async def select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        platform = select.values[0]
        await interaction.response.send_message(self.instructions[platform], ephemeral=True)

# Helper function to close a ticket and generate transcript, usable by both staff and close button
async def close_ticket_action(interaction: discord.Interaction, bot):
    # Defer immediately if not already deferred by a button click
    if not interaction.response.is_done():
        await interaction.response.defer(thinking=True) # Public defer since this is a closing action
    else:
        # If already deferred, send a followup message to indicate processing
        await interaction.followup.send("`Saving ticket and preparing transcript...`", ephemeral=True)
    
    # Reliably get the ticket creator's ID from the bot's active ticket cache
    ticket_state = bot.active_tickets.get(interaction.channel.id, {})
    ticket_creator_id = ticket_state.get("creator_id")
    ticket_creator = None
    if ticket_creator_id:
        try:
            ticket_creator = await bot.fetch_user(ticket_creator_id)
        except discord.NotFound:
            print(f"Original ticket creator {ticket_creator_id} not found during transcript close.")
        except Exception as e:
            print(f"Error fetching ticket creator {ticket_creator_id}: {type(e).__name__}: {e}")

    # Get transcript channel from config
    transcript_channel_id = bot.config.get("ticket_transcripts_channel_id")
    transcript_channel = None
    if transcript_channel_id:
        transcript_channel = bot.get_channel(transcript_channel_id)
        if not transcript_channel or not isinstance(transcript_channel, discord.TextChannel):
            print(f"Transcript channel (ID: {transcript_channel_id}) not found or is not a text channel.")
            transcript_channel = None # Ensure it's None if invalid or not found

    filepath = f"logs/transcripts/transcript-{interaction.channel.id}.html"
    transcript_file_sent = False # Flag to track if transcript was successfully sent to staff

    try:
        os.makedirs('logs/transcripts', exist_ok=True) # Ensure directory exists
        
        # Export chat as HTML using chat_exporter
        transcript_html = await chat_exporter.export(
            interaction.channel,
            # Add options here if needed, e.g., dark_mode=True
        )
        
        if transcript_html:
            # Generate AI Summary if AI Chatbot cog is available
            ai_cog = bot.get_cog("AIChatbot")
            if ai_cog:
                # Fetch history for summary. Limit to a reasonable number.
                # History is fetched newest first, needs to be reversed for chronological order for LLM context.
                history_for_summary = [msg async for msg in interaction.channel.history(limit=250)]
                history_for_summary.reverse() 
                summary = await ai_cog.generate_summary(history_for_summary)
                
                # Inject AI summary into the HTML transcript
                # This is a basic string replacement, might need adjustment if chat_exporter's HTML structure changes
                summary_html = f'<div style="background-color: #2b2d31; color: #ffffff; padding: 15px; margin: 10px 0; border-radius: 5px; border: 1px solid #404249;"><b>AI Summary:</b> {summary}</div>'
                transcript_html = transcript_html.replace('<body>', f'<body>{summary_html}')
            else:
                print("AIChatbot cog not found, skipping AI summary generation.")

            # Save the HTML to a file
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(transcript_html)

            # Send transcript to staff channel
            if transcript_channel:
                try:
                    staff_embed = discord.Embed(
                        title="Ticket Transcript Saved",
                        description=f"Ticket `{interaction.channel.name}` (ID: {interaction.channel.id}) closed by {interaction.user.mention}.",
                        color=int(bot.config['error_color'], 16), # Using error_color for closed ticket log
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    staff_file = discord.File(filepath, filename=f"transcript-{interaction.channel.id}.html")
                    await transcript_channel.send(embed=staff_embed, file=staff_file, view=TranscriptInstructionsView())
                    transcript_file_sent = True
                    print(f"Transcript sent to staff channel for ticket {interaction.channel.id}.")
                except discord.Forbidden:
                    print(f"Bot lacks permissions to send transcript to staff channel {transcript_channel_id}.")
                    await interaction.followup.send("⚠️ Transcript generated but could not be sent to staff channel (permissions error).", ephemeral=True)
                except Exception as e:
                    print(f"Error sending transcript to staff channel: {type(e).__name__}: {e}")
                    await interaction.followup.send("⚠️ Transcript generated but an error occurred sending to staff channel.", ephemeral=True)
            else:
                await interaction.followup.send("⚠️ Transcript channel not configured or found. Transcript generated but not sent to staff.", ephemeral=True)


            # Send transcript to ticket creator via DM
            if ticket_creator:
                try:
                    customer_embed = discord.Embed(
                        title="Your Ticket Has Been Closed",
                        description="Thank you for contacting us. A transcript of your conversation is attached for your reference.",
                        color=int(bot.config['embed_color'], 16), # Using embed_color for customer DM
                        timestamp=datetime.datetime.now(datetime.timezone.utc)
                    )
                    customer_file = discord.File(filepath, filename=f"transcript-{interaction.channel.id}.html")
                    await ticket_creator.send(embed=customer_embed, file=customer_file, view=TranscriptInstructionsView())
                    print(f"Transcript DM sent to ticket creator {ticket_creator.id}.")
                except discord.Forbidden:
                    await interaction.followup.send(f"⚠️ Could not send transcript DM to `{ticket_creator.display_name}` (DMs disabled).", ephemeral=True)
                    print(f"Could not send transcript DM to {ticket_creator.id}: DMs disabled.")
                except Exception as e:
                    await interaction.followup.send("⚠️ Transcript generated but an error occurred sending DM to you.", ephemeral=True)
                    print(f"Error sending customer transcript DM to {ticket_creator.id}: {type(e).__name__}: {e}")
            else:
                await interaction.followup.send("⚠️ Could not identify the original ticket creator to send a transcript DM.", ephemeral=True)

        else: # If transcript_html is empty (e.g., no messages in thread)
            await interaction.followup.send("❌ Failed to generate transcript (no messages in ticket or export failed).", ephemeral=True)
            if transcript_channel:
                try:
                    await transcript_channel.send(f"⚠️ Failed to generate transcript for `{interaction.channel.name}` (ID: {interaction.channel.id}). Channel might have been empty.")
                except discord.Forbidden:
                    print(f"Bot lacks permissions to send error message to staff channel {transcript_channel_id}.")

    except Exception as e:
        print(f"Critical error during transcript generation/sending for channel {interaction.channel.id}: {type(e).__name__}: {e}")
        await interaction.followup.send(f"❌ An unexpected critical error occurred while processing the transcript. Please try again or manually save chat history.", ephemeral=True)
        # Notify staff channel about critical transcript failure if it's available
        if transcript_channel:
            try:
                await transcript_channel.send(f"❌ Critical error for ticket {interaction.channel.mention} (ID: {interaction.channel.id}) during transcript: `{type(e).__name__}: {e}`")
            except discord.Forbidden:
                print(f"Bot lacks permissions to send critical error message to staff channel {transcript_channel_id}.")
    finally:
        # Always clean up active_tickets and delete channel after attempts, even if transcript failed
        bot.active_tickets.pop(interaction.channel.id, None)
        print(f"Removed ticket {interaction.channel.id} from active_tickets cache.")
        
        # Give a moment for messages to send before deleting the channel
        await asyncio.sleep(3) 
        try:
            await interaction.channel.delete(reason=f"Ticket closed by {interaction.user.display_name} (Transcript status: {'Sent' if transcript_file_sent else 'Failed'})")
            print(f"Deleted ticket channel {interaction.channel.id}.")
        except discord.NotFound:
            print(f"Ticket channel {interaction.channel.id} already deleted. Ignoring.")
            pass 
        except discord.Forbidden:
            print(f"Bot lacks permissions to delete ticket channel {interaction.channel.id}.")
            await interaction.followup.send("⚠️ I don't have permission to delete this ticket channel. Please delete it manually.", ephemeral=True)
        except Exception as e:
            print(f"Error deleting ticket channel {interaction.channel.id}: {type(e).__name__}: {e}")
            await interaction.followup.send(f"❌ An unexpected error occurred while deleting the ticket channel: {type(e).__name__}: {e}", ephemeral=True)


# --- UI COMPONENTS FOR BUY TICKETS ---
class ProductSelect(discord.ui.Select):
    def __init__(self, bot, products): # products passed from ShoppingCartView init
        self.bot = bot
        # Only show products that have stock > 0 or are infinite (-1)
        # Ensure 'name', 'price', 'emoji' keys exist with defaults
        options = []
        for pid, prod in products.items():
            try:
                stock = int(prod.get('stock', 0)) # Explicitly convert stock to int here
            except ValueError:
                print(f"Warning: Product {pid} has non-integer stock '{prod.get('stock')}'. Skipping for ProductSelect.")
                continue # Skip this product if stock is not a valid integer

            if stock > 0 or stock == -1:
                options.append(
                    discord.SelectOption(
                        label=prod.get('name', 'Unnamed Product')[:100], # Truncate label to 100 characters for Discord limit
                        value=pid, 
                        description=f"Price: ₹{prod.get('price', 0.0):.2f}" if prod.get('price') is not None else "Custom Quote", 
                        emoji=prod.get('emoji') # Custom emoji directly from config/product data
                    )
                )
        
        options = options[:25] # Limit to 25 options per Discord's API limits for select menus

        if not options:
            options.append(discord.SelectOption(label="No products available at the moment.", value="disabled", emoji="❌"))
        super().__init__(placeholder="Select a product to add to your cart...", options=options, custom_id="product_select")

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "disabled":
            await interaction.response.send_message("There are currently no products to choose from.", ephemeral=True)
            return
        
        # Defer immediately as this can involve multiple async operations and message edits
        await interaction.response.defer(thinking=True, ephemeral=True) 
        
        ticket_state = self.bot.active_tickets.get(interaction.channel.id)
        if not ticket_state:
            await interaction.followup.send("❌ This ticket is no longer active. Please open a new one if needed.", ephemeral=True)
            return

        cart = ticket_state.get("cart", {})
        products_db = await self.bot.load_json('products') # Load products fresh to get latest info (e.g., price, stock)
        product_id = self.values[0]
        product = products_db.get(product_id)
        
        if not product:
            await interaction.followup.send("❌ Selected product not found or no longer exists. It may have been removed or renamed. Please select another.", ephemeral=True)
            return
            
        # Check if adding the item exceeds available stock
        try:
            stock = int(product.get('stock', 0)) # Explicitly convert stock to int here
        except ValueError:
            await interaction.followup.send(f"⚠️ Product '{product.get('name', product_id)}' has invalid stock data. Please contact staff.", ephemeral=True)
            return

        if stock != -1: # -1 is infinite stock, so no check needed
            current_quantity_in_cart = cart.get(product_id, {}).get('quantity', 0)
            if current_quantity_in_cart >= stock:
                await interaction.followup.send(f"⚠️ You cannot add more of **{product['name']}**. All available stock is already in your cart.", ephemeral=True)
                return

        if product_id in cart: 
            cart[product_id]['quantity'] += 1
        else: 
            # Ensure price exists for new items, default to 0.0 if not set in product
            cart[product_id] = {"name": product['name'], "price": product.get('price', 0.0), "quantity": 1}
        
        ticket_state["cart"] = cart
        self.bot.active_tickets[interaction.channel.id] = ticket_state # Update active_tickets in memory

        # Dynamic Ticket Renaming Logic (only for private threads)
        if isinstance(interaction.channel, discord.Thread) and interaction.channel.type == discord.ChannelType.private_thread:
            try:
                # Get the first item's name for the thread title for simplicity and brevity
                # Use .get() with a default to prevent KeyError if 'name' is missing
                first_item_name = next(iter(cart.values())).get('name', 'Product')
                # Sanitize name for Discord channel naming (alphanumeric, hyphens only, no special chars)
                sanitized_name = re.sub(r'[^a-zA-Z0-9-]', '', first_item_name).strip()
                if not sanitized_name: 
                    sanitized_name = "product" # Fallback if name becomes empty after sanitization
                
                # Construct base name and ensure it fits Discord's 100-character limit
                # Consider space for emoji, username, and quantity (e.g., "🛒-User-Item-xQ")
                base_info = f"🛒-{interaction.user.name}-"
                max_item_name_len = 100 - len(base_info) - (len(str(cart[product_id]['quantity'])) + 1) # Space for -xQ
                item_part = sanitized_name[:max_item_name_len]
                
                new_name = f"{base_info}{item_part}-x{cart[product_id]['quantity']}"
                new_name = new_name[:100].strip('- ') # Final truncation and cleanup, remove trailing hyphens/spaces
                
                # To avoid hitting rate limits, only edit if the name is different
                if interaction.channel.name != new_name:
                    await interaction.channel.edit(name=new_name)
            except Exception as e:
                print(f"Could not rename thread {interaction.channel.id}: {type(e).__name__}: {e}") # Log error but don't stop the flow

        # Update the cart embed in the thread.
        # Use the stored cart_message_id from ticket_state if available, otherwise try to find it.
        cart_message_id = ticket_state.get('cart_message_id')
        cart_message = None
        if cart_message_id:
            try:
                cart_message = await interaction.channel.fetch_message(cart_message_id)
            except discord.NotFound:
                print(f"Cart message {cart_message_id} not found in channel {interaction.channel.id} (might be deleted).")
                cart_message_id = None # Invalidate ID if message not found
            except Exception as e:
                print(f"Error fetching cart message {cart_message_id}: {type(e).__name__}: {e}")
                cart_message_id = None
        
        # If message not found by ID or ID was stale, try to find it in recent history
        if not cart_message:
            # Look for recent bot messages with components (likely the cart message)
            messages_history = [msg async for msg in interaction.channel.history(limit=50) if msg.author == self.bot.user and msg.components]
            for msg in messages_history:
                # Check if the message has components belonging to a ShoppingCartView
                if any(isinstance(comp, discord.ui.Item) and comp.custom_id in ["product_select", "cart_confirm", "cart_discount", "cart_cancel"] 
                       for row in msg.components for comp in row.children): # Iterate through component rows and children
                    cart_message = msg
                    ticket_state['cart_message_id'] = cart_message.id # Store newly found message ID
                    self.bot.active_tickets[interaction.channel.id] = ticket_state # Update active_tickets
                    break

        if cart_message:
            # Call update_cart_embed on the main ShoppingCartView instance (self.view)
            # Pass the interaction and the message to edit.
            await self.view.update_cart_embed(interaction=interaction, message_to_edit=cart_message)
            await interaction.followup.send(f"✅ Added **{product['name']}** to your cart!", ephemeral=True)
        else:
            await interaction.followup.send("✅ Added product to cart, but could not find the main cart message to update visually. Please check the ticket for the updated cart total.", ephemeral=True)


class DiscountCodeModal(discord.ui.Modal, title="Apply Discount Code"):
    def __init__(self, bot, channel_id: int): # Pass bot and channel_id instead of ShoppingCartView for cleaner modal
        super().__init__()
        self.bot = bot
        self.channel_id = channel_id # Store channel_id to access ticket_state
        self.code_input = discord.ui.TextInput(
            label="Enter your discount code",
            placeholder="e.g., REDEEM-ABC123 or SUMMER25",
            min_length=5,
            required=True # Make code input required
        )
        self.add_item(self.code_input)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True) # Defer immediately for modal submission
        code = self.code_input.value.strip().upper()
        
        ticket_state = self.bot.active_tickets.get(self.channel_id)
        if not ticket_state:
            await interaction.followup.send("❌ This ticket is no longer active or its state was lost. Please open a new one if needed.", ephemeral=True)
            return

        if ticket_state.get('discount', 0.0) > 0.0:
            await interaction.followup.send("❌ A discount has already been applied to this order. Only one discount can be applied per order.", ephemeral=True)
            return

        # Handle referral codes first (priority over general discounts)
        if code.startswith("REF-"):
            referrals = await self.bot.load_json('referrals') # Load referral codes data
            referrer_id_str = referrals.get(code) # Referral codes map code to referrer's user ID (string in JSON)
            
            if not referrer_id_str:
                await interaction.followup.send("❌ Invalid referral code. This code does not exist.", ephemeral=True)
                return
            
            # Ensure referrer_id is an integer for direct comparison with Discord user ID
            try:
                referrer_id = int(referrer_id_str)
            except ValueError:
                await interaction.followup.send("❌ Corrupted referral code data. Please contact staff.", ephemeral=True)
                return

            if interaction.user.id == referrer_id:
                await interaction.followup.send("❌ You cannot use your own referral code for a new customer discount.", ephemeral=True)
                return

            # Check if user is a new customer (no prior delivered orders)
            orders = await self.bot.load_json('orders') # Using bot's load_json
            has_past_orders = any(o.get('user_id') == interaction.user.id and o.get('status') == 'Delivered' for o in orders.values())
            if has_past_orders:
                await interaction.followup.send("❌ Referral codes are for **new customers only** (users without prior delivered orders).", ephemeral=True)
                return

            # Correctly access nested config for affiliate program discount amounts
            affiliate_config = self.bot.config.get('loyalty_program', {}).get('affiliate_program', {})
            discount_amount = affiliate_config.get('new_user_discount_inr')
            
            if discount_amount is None:
                await interaction.followup.send("❌ Referral program is not configured correctly (new user discount amount missing). Please contact staff.", ephemeral=True)
                return
            if discount_amount <= 0:
                await interaction.followup.send("❌ Referral program discount amount must be positive. Please contact staff.", ephemeral=True)
                return

            # Apply the discount to the current ticket state
            ticket_state['discount'] = discount_amount
            ticket_state['discount_reason'] = f"Referral Discount (Code: {code})" # Store reason for transparency
            ticket_state['referral_info'] = {"code": code, "referrer_id": referrer_id} # Store referral details
            self.bot.active_tickets[self.channel_id] = ticket_state # Update active_tickets in memory

            await interaction.followup.send(f"✅ Success! A new customer discount of **₹{discount_amount:.2f}** has been applied to your order.", ephemeral=True)
            
            # Now, find the ShoppingCartView message in the channel and update its embed
            # Use the stored cart_message_id if available, or try to find it
            cart_message_id = ticket_state.get('cart_message_id')
            cart_message = None
            if cart_message_id:
                try:
                    cart_message = await interaction.channel.fetch_message(cart_message_id)
                except discord.NotFound:
                    print(f"Cart message {cart_message_id} not found for update after discount modal.")
            
            if cart_message:
                # To call update_cart_embed, we need an instance of ShoppingCartView.
                # Since the view is persistent, we can try to get its instance from bot.persistent_views.
                # If not found (e.g., bot restarted or view not added persistently), create a dummy one.
                sc_view_instance = discord.utils.get(self.bot.persistent_views, custom_id="persistent_shopping_cart_view")
                if not sc_view_instance:
                    print("Warning: Persistent ShoppingCartView not found. Creating dummy for update.")
                    dummy_products_for_view = await self.bot.load_json('products') # Pass products for ProductSelect options
                    sc_view_instance = ShoppingCartView(self.bot, dummy_products_for_view)
                
                await sc_view_instance.update_cart_embed(interaction=interaction, message_to_edit=cart_message)
            else:
                await interaction.followup.send("⚠️ Applied discount, but could not find the main cart message to update visually. Please check the ticket for the updated cart total.", ephemeral=True)
            return

        # Handle regular discount codes (redeemable or promotional)
        discounts_db = await self.bot.load_json('discounts') # Load current discounts data
        discount_info = discounts_db.get(code)

        if not discount_info:
            await interaction.followup.send("❌ That discount code is invalid or does not exist.", ephemeral=True)
            return

        code_type = discount_info.get("type", "promo") # Default to 'promo' if type is missing, or 'redeem' if that's more common
        
        if code_type == "redeem":
            if discount_info.get("used"):
                await interaction.followup.send("❌ That redemption code has already been used.", ephemeral=True)
                return
            # Optional: Check if this redemption code was generated for *this specific user* (e.g., from loyalty points)
            # This 'generated_by' check is good for ensuring personal codes aren't shared
            if 'generated_by' in discount_info and str(interaction.user.id) != str(discount_info['generated_by']):
                await interaction.followup.send("❌ This redemption code was not generated for your account and cannot be used.", ephemeral=True)
                return
            discounts_db[code]['used'] = True # Mark as used in the database
        
        elif code_type == "promo":
            if not discount_info.get("is_active", True): # Check if promo code is still active
                await interaction.followup.send("❌ This promotional code is no longer active.", ephemeral=True)
                return
            
            max_uses = discount_info.get("max_uses", float('inf')) # Get max uses, default to infinite
            current_uses = discount_info.get("uses", 0)
            if max_uses != float('inf') and current_uses >= max_uses:
                await interaction.followup.send("❌ This promotional code has reached its maximum number of uses.", ephemeral=True)
                return
            
            if expiry_str := discount_info.get("expires_at"):
                try:
                    expiry_time = datetime.datetime.fromisoformat(expiry_str)
                    if expiry_time < datetime.datetime.now(datetime.timezone.utc):
                        await interaction.followup.send("❌ This promotional code has expired.", ephemeral=True)
                        return
                except ValueError:
                    print(f"Warning: Invalid 'expires_at' format for discount code {code}: {expiry_str}. Treating as non-expiring.")
                    pass # Continue if timestamp format is bad, assuming it means no expiry

            discounts_db[code]['uses'] = current_uses + 1 # Increment uses for promo code
        
        else: # Handle unknown code types if any
            await interaction.followup.send("❌ Unknown discount code type. Please contact staff.", ephemeral=True)
            return

        discount_amount = discount_info.get('discount_inr')
        if discount_amount is None:
            await interaction.followup.send("❌ Discount amount not specified for this code. Please contact staff.", ephemeral=True)
            return
        if discount_amount <= 0:
            await interaction.followup.send("❌ Discount amount must be positive. Please contact staff.", ephemeral=True)
            return

        # Apply the discount to the current ticket state
        ticket_state['discount'] = discount_amount
        ticket_state['discount_reason'] = f"Discount Code ({code})" # Store reason
        self.bot.active_tickets[self.channel_id] = ticket_state
        
        await self.bot.save_json('discounts', discounts_db) # Save updated discounts data
        
        await interaction.followup.send(f"✅ Success! A discount of **₹{discount_amount:.2f}** has been applied to your order.", ephemeral=True)
        
        # Now, find the ShoppingCartView message in the channel and update its embed
        # Use the stored cart_message_id if available, or try to find it
        cart_message_id = ticket_state.get('cart_message_id')
        cart_message = None
        if cart_message_id:
            try:
                cart_message = await interaction.channel.fetch_message(cart_message_id)
            except discord.NotFound:
                print(f"Cart message {cart_message_id} not found for update after discount modal.")
        
        if cart_message:
            # Get the persistent view instance or create a dummy for updating
            sc_view_instance = discord.utils.get(self.bot.persistent_views, custom_id="persistent_shopping_cart_view")
            if not sc_view_instance:
                print("Warning: Persistent ShoppingCartView not found. Creating dummy for update after discount.")
                dummy_products_for_view = await self.bot.load_json('products')
                sc_view_instance = ShoppingCartView(self.bot, dummy_products_for_view)
            
            await sc_view_instance.update_cart_embed(interaction=interaction, message_to_edit=cart_message)
        else:
            await interaction.followup.send("⚠️ Applied discount, but could not find the main cart message to update visually. Please check the ticket for the updated cart total.", ephemeral=True)


class ShoppingCartView(discord.ui.View):
    def __init__(self, bot, products): # products will be passed as {} during add_view in main
        super().__init__(timeout=None) # Persists across restarts
        self.bot = bot
        self.custom_id = "persistent_shopping_cart_view" # Custom ID for persistence\
        self.add_item(ProductSelect(bot, products))

    async def update_cart_embed(self, interaction: discord.Interaction, message_to_edit: discord.Message = None):
        """
        Updates the shopping cart embed with the current cart contents and total.
        This function is crucial for keeping the UI in sync with the bot's state.
        It must be called reliably after any cart modification.
        """
        ticket_state = self.bot.active_tickets.get(interaction.channel.id)
        if not ticket_state:
            # Fallback: if state is somehow lost, re-initialize minimally or exit gracefully
            # A more robust system would try to reload state from a persistent file based on channel ID
            print(f"Warning: Ticket state for channel {interaction.channel.id} not found during update_cart_embed.")
            return # Cannot update cart if state is gone

        cart = ticket_state.get("cart", {})
        discount = ticket_state.get("discount", 0.0)
        discount_reason = ticket_state.get('discount_reason', 'Discount Applied') # Get the reason for the discount
        embed = discord.Embed(
            title="🛒 Your Shopping Cart", 
            color=int(self.bot.config['embed_color'], 16),
            timestamp=datetime.datetime.now(datetime.timezone.utc) # Add timestamp for freshness
        )
        
        if not cart:
            embed.description = "Your cart is empty. Select a product from the dropdown to begin."
            embed.set_footer(text="Grand Total: ₹0.00")
        else:
            total = sum(i.get('price', 0.0) * i.get('quantity', 0) for i in cart.values()) # Ensure price is float
            description_lines = []
            for product_id, item_data in cart.items():
                product_name = item_data.get('name', 'Unknown Product')
                quantity = item_data.get('quantity', 0)
                price_per_unit = item_data.get('price', 0.0)
                item_total = price_per_unit * quantity
                description_lines.append(f"**{product_name}** x{quantity} - `₹{item_total:.2f}`")
            
            description = "\n".join(description_lines)
            
            if discount > 0.0:
                description += f"\n\n**{discount_reason}:** `-₹{discount:.2f}`"
            
            embed.description = description
            final_total = max(0.0, total - discount) # Ensure final total is not negative
            embed.set_footer(text=f"Grand Total: ₹{final_total:.2f}")

        # Always re-render the ProductSelect dropdown options with fresh data
        current_products_for_view = await self.bot.load_json('products')
        self.children[0] = ProductSelect(self.bot, current_products_for_view) # Re-assigns the ProductSelect instance

        # Ensure the view is always updated on the original message.
        # This is the most crucial part for button interactions to work correctly.
        if message_to_edit:
            try:
                # Removed 'attachments=[file_for_embed]' as it's not always defined here.
                await message_to_edit.edit(embed=embed, view=self) 
                print(f"Cart message {message_to_edit.id} updated successfully.")
            except discord.NotFound:
                print(f"Error: Cart message {message_to_edit.id} not found during edit. It might have been deleted. Sending ephemeral followup.")
                await interaction.followup.send(embed=embed, ephemeral=True)
            except discord.Forbidden:
                print(f"Error: Bot lacks permissions to edit message {message_to_edit.id}. Sending ephemeral followup.")
                await interaction.followup.send(embed=embed, ephemeral=True)
            except Exception as e:
                print(f"An unexpected error occurred editing cart message {message_to_edit.id}: {type(e).__name__}: {e}")
                await interaction.followup.send(embed=embed, ephemeral=True)

        else:
            print("Warning: update_cart_embed called without message_to_edit. Attempting direct interaction response.")
            if not interaction.response.is_done():
                await interaction.response.edit_original_response(embed=embed, view=self)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)


    @discord.ui.button(label="Confirm Order", style=discord.ButtonStyle.primary, emoji="<:ib_yes:1393834020470521876>", custom_id="cart_confirm", row=1)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=False) # Defer publicly as this is a major action
            ticket_state = self.bot.active_tickets.get(interaction.channel.id)
            
            if not ticket_state or not ticket_state.get("cart"):
                await interaction.followup.send("Your cart is empty. Cannot confirm an empty order. Please add products first.", ephemeral=True)
                return
                
            cart_contents = ticket_state.get("cart", {})
            if not cart_contents: # Double-check cart is not empty
                await interaction.followup.send("Your cart is empty. Cannot confirm order.", ephemeral=True)
                return

            # Check product stock before confirming order, prevent overselling
            products_db = await self.bot.load_json('products')
            for pid, item_data in cart_contents.items():
                product_info = products_db.get(pid)
                if not product_info:
                    await interaction.followup.send(f"❌ Product '{item_data.get('name', pid)}' not found in store. Cannot confirm order. Please remove it from your cart.", ephemeral=True)
                    return
                try:
                    stock_available = int(product_info.get('stock', 0)) # Ensure stock is int
                except ValueError:
                    await interaction.followup.send(f"❌ Product '{product_info.get('name', pid)}' has invalid stock data. Please contact staff.", ephemeral=True)
                    return

                quantity_in_cart = item_data.get('quantity', 0)
                if stock_available != -1 and quantity_in_cart > stock_available: # -1 is infinite
                    await interaction.followup.send(f"❌ We only have {stock_available} of '{product_info.get('name', pid)}' in stock, but your cart has {quantity_in_cart}. Please adjust the quantity in your cart.", ephemeral=True)
                    return

            # --- Tiered Pricing / Role-Based Discount Logic ---
            final_discount = ticket_state.get('discount', 0.0)
            # Only apply role discount if a manual discount code hasn't been used yet (final_discount is 0.0)
            # and if the user is a discord.Member (i.e., in a guild where roles apply)
            if final_discount == 0.0 and isinstance(interaction.user, discord.Member):
                user_roles = {r.id for r in interaction.user.roles} # Use a set for faster lookup
                role_discounts_config = self.bot.config.get('role_based_discounts', [])
                
                # Sort discounts by percentage, highest first, to apply the best one if multiple roles apply
                sorted_role_discounts = sorted(role_discounts_config, key=lambda r: r.get('discount_percent', 0), reverse=True)
                
                total_cart_value = sum(item.get('price', 0.0) * item.get('quantity', 0) for item in cart_contents.values())

                for r_discount in sorted_role_discounts:
                    role_id = r_discount.get('role_id')
                    discount_percent = r_discount.get('discount_percent')
                    
                    if role_id and discount_percent is not None and role_id in user_roles:
                        # Calculate discount based on cart total
                        calculated_discount = total_cart_value * (discount_percent / 100.0)
                        if calculated_discount > final_discount: # Apply only if it's a better discount than any previous role-based one
                            final_discount = calculated_discount
                            ticket_state['discount'] = final_discount
                            ticket_state['discount_reason'] = f"{discount_percent}% Role Discount (for <@&{role_id}>)"
                        break # Apply the single best discount and stop
            
            # Ensure discount is applied to ticket_state for saving
            ticket_state['discount'] = final_discount
            self.bot.active_tickets[interaction.channel.id] = ticket_state

            # Update the embed to show the final discount before disabling buttons
            # Pass interaction.message as message_to_edit to ensure the current message with buttons is updated.
            await self.update_cart_embed(interaction=interaction, message_to_edit=interaction.message)
            
            # Disable all components in the view for current interaction's message
            for item in self.children:
                item.disabled = True
            await interaction.message.edit(view=self) # Edit the original message to disable buttons after confirmation
            
            # Generate Order ID
            counters = await self.bot.load_json('counters') # Using bot's load_json
            last_order_number = counters.get('last_order_number', 0)
            new_order_number = last_order_number + 1
            order_id = f"ORD{new_order_number:04d}"
            counters['last_order_number'] = new_order_number
            await self.bot.save_json('counters', counters) # Using bot's save_json

            # Save the final order details
            orders = await self.bot.load_json('orders') # Using bot's load_json
            orders[order_id] = {
                "user_id": interaction.user.id, 
                "items": cart_contents, # Use the confirmed cart contents
                "status": "Pending Payment", 
                "discount": final_discount,
                "discount_reason": ticket_state.get('discount_reason', 'No Discount'), # Save the reason for the discount
                "gift_recipient_id": ticket_state.get('gift_recipient_id'), # Store recipient ID if gifting
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(), # Use current UTC time
                "channel_id": interaction.channel.id # Store channel ID for later lookup/notification
            }
            ticket_state['order_id'] = order_id # Store order_id in active_tickets for quick reference
            self.bot.active_tickets[interaction.channel.id] = ticket_state # Update active_tickets
            await self.bot.save_json('orders', orders) # Using bot's save_json
            
            # Generate Payment Link using PaymentGateway cog
            payment_cog = self.bot.get_cog('PaymentGateway')
            if not payment_cog:
                await interaction.followup.send("❌ **Critical Error:** Payment gateway is not loaded. Please contact staff to complete your order.", ephemeral=True)
                return

            embed, file, view = await payment_cog.generate_payment_embed(order_id, interaction.user, cart_contents, final_discount)
            
            # Send the payment embed publicly in the ticket channel
            payment_message_content = f"{interaction.user.mention}, your order (`#{order_id}`) has been confirmed! Please make your payment using the details below."
            if ticket_state.get('gift_recipient_id'):
                recipient = await self.bot.fetch_user(ticket_state['gift_recipient_id'])
                if recipient: payment_message_content += f"\nThis order is a gift for {recipient.mention}."
                
            await interaction.followup.send(content=payment_message_content, embed=embed, file=file, view=view)

        except Exception as e:
            print(f"Error in confirm button callback for ticket {interaction.channel.id}: {type(e).__name__}: {e}")
            await interaction.followup.send(f"❌ An unexpected error occurred while confirming your order: {type(e).__name__}: {e}. Please try again or contact staff.", ephemeral=True)


    @discord.ui.button(label="Apply Discount", style=discord.ButtonStyle.secondary, emoji="<:ib_greenstars:1395711946195734558>", custom_id="cart_discount", row=1)
    async def apply_discount(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            # Send the DiscountCodeModal. The modal handles its own deferral.
            await interaction.response.send_modal(DiscountCodeModal(self.bot, interaction.channel.id))
        except Exception as e:
            print(f"Error in apply_discount button callback for ticket {interaction.channel.id}: {type(e).__name__}: {e}")
            if not interaction.response.is_done():
                await interaction.response.send_message(f"❌ An unexpected error occurred: {type(e).__name__}: {e}", ephemeral=True)
            else:
                await interaction.followup.send(f"❌ An unexpected error occurred: {type(e).__name__}: {e}", ephemeral=True)


    @discord.ui.button(label="Cancel Order", style=discord.ButtonStyle.danger, emoji="✖️", custom_id="cart_cancel", row=1)
    async def cancel_order(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            await interaction.response.defer(ephemeral=False) # Defer publicly as it might edit main message
            ticket_state = self.bot.active_tickets.get(interaction.channel.id)
            if not ticket_state:
                await interaction.followup.send("This ticket is no longer active or its state was lost. No order to cancel.", ephemeral=True)
                return

            order_id = ticket_state.get('order_id')
            
            if not order_id:
                await interaction.followup.send("You can only cancel an order after it has been confirmed (i.e., after clicking 'Confirm Order') and before payment is received. If you wish to close the ticket, please use the staff controls.", ephemeral=True)
                return

            orders_db = await self.bot.load_json('orders') # Load current orders data
            order_to_cancel = orders_db.get(order_id)

            if order_to_cancel and order_to_cancel.get('status') == "Pending Payment":
                order_to_cancel['status'] = "Cancelled by User" # Update status
                orders_db[order_id] = order_to_cancel # Update the order in the main dict
                await self.bot.save_json('orders', orders_db) # Save updated orders data
                
                # --- IMPORTANT: If a redeemable discount code was used, consider un-using it ---
                # This logic needs to be careful not to create vulnerabilities.
                # For simplicity, if a 'redeem' type code was used, we mark it unused ONLY if it was generated by the current user.
                # More complex logic might track order ID per discount use.
                if ticket_state.get('discount_reason') and "REDEEM-" in ticket_state['discount_reason']:
                    # Extract the code from the discount reason string, assuming format "Discount Code (CODE)"
                    match = re.search(r'\(([^)]+)\)', ticket_state['discount_reason'])
                    used_code = match.group(1) if match else None

                    if used_code:
                        discounts_db = await self.bot.load_json('discounts')
                        # Check if it's a redeem type code, was marked used, and was generated by this user
                        if used_code in discounts_db and discounts_db[used_code].get('type') == 'redeem' and \
                           discounts_db[used_code].get('used') and str(discounts_db[used_code].get('generated_by')) == str(interaction.user.id):
                            discounts_db[used_code]['used'] = False # Mark as unused
                            await self.bot.save_json('discounts', discounts_db)
                            print(f"Redeem code {used_code} marked as unused due to order cancellation by {interaction.user.id}.")

                # Clear order-related info from in-memory active_tickets state
                ticket_state.pop('order_id', None)
                ticket_state.pop('discount', 0.0) # Reset discount
                ticket_state.pop('discount_reason', 'No Discount') # Reset discount reason
                ticket_state.pop('gift_recipient_id', None) # Clear gift recipient
                self.bot.active_tickets[interaction.channel.id] = ticket_state # Update active_tickets

                # Re-enable cart buttons for the current message and update embed
                for item in self.children:
                    item.disabled = False
                
                # Find the message with the ShoppingCartView and update it
                cart_message_id = ticket_state.get('cart_message_id')
                cart_message = None
                if cart_message_id:
                    try:
                        cart_message = await interaction.channel.fetch_message(cart_message_id)
                    except discord.NotFound:
                        print(f"Cart message {cart_message_id} not found for update after cancellation.")
                
                if cart_message:
                    await self.update_cart_embed(interaction=interaction, message_to_edit=cart_message)
                
                await interaction.followup.send("✅ Your order has been cancelled. You can now modify your cart or create a new order.", ephemeral=True)
                await interaction.channel.send(f"This order (`#{order_id}`) has been cancelled by {interaction.user.mention}.")
            else:
                await interaction.followup.send("❌ This order can no longer be cancelled (it might not be pending payment, or has already been delivered). Please contact staff for assistance if you believe this is an error.", ephemeral=True)
        except Exception as e:
            print(f"Error in cancel_order button callback for ticket {interaction.channel.id}: {type(e).__name__}: {e}")
            await interaction.followup.send(f"❌ An unexpected error occurred while cancelling your order: {type(e).__name__}: {e}. Please try again or contact staff.", ephemeral=True)
# --- UI COMPONENTS FOR SUPPORT TICKETS ---
class SupportIssueModal(discord.ui.Modal, title="Describe Your Issue"):
    def __init__(self, bot, order_id: str = None): # order_id is optional for general support tickets
        super().__init__()
        self.bot = bot
        self.order_id = order_id
        
        # Add a title field if it's a general inquiry (no specific order)
        self.inquiry_title_input = None
        if not order_id:
            self.inquiry_title_input = discord.ui.TextInput(
                label="Brief Title for your Inquiry",
                placeholder="e.g., General Question, Account Help",
                required=True,
                max_length=100
            )
            self.add_item(self.inquiry_title_input) # Add to modal if it's a general inquiry

        self.issue_description = discord.ui.TextInput(
            label="Please describe your issue in detail", 
            style=discord.TextStyle.paragraph, 
            placeholder="e.g., My login credentials are not working for order #ORD1234. I've tried XYZ steps.",
            required=True
        )
        self.add_item(self.issue_description)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True) # Defer immediately for modal
        
        order = None
        items_str = "N/A (General Inquiry)"
        # Determine the title for the embed based on whether it's an order-specific or general inquiry
        issue_title_display = self.inquiry_title_input.value if self.inquiry_title_input else f"Support for Order #{self.order_id}"

        if self.order_id:
            orders_db = await self.bot.load_json('orders') # Load orders fresh
            order = orders_db.get(self.order_id)
            if not order: # Should not happen if select menu works correctly, but good for safety
                await interaction.followup.send("❌ The selected order was not found. Please try again or open a general support ticket.", ephemeral=True)
                return
            items_str = ", ".join([item.get('name', 'Unknown Product') for item in order.get('items', {}).values()])
        
        ai_cog = self.bot.get_cog("AIChatbot")
        ai_response = "AI Assistant is currently unavailable or encountered an error."
        if ai_cog:
            # Ensure the AI cog is fully loaded before making a call
            await self.bot.wait_until_ready() 
            try:
                # If order_id, use product info. Otherwise, just issue description.
                if self.order_id:
                    ai_response = await ai_cog.generate_support_suggestion(items_str, self.issue_description.value)
                else: # For general inquiry
                    ai_response = await ai_cog.generate_support_suggestion(issue_title_display, self.issue_description.value)
            except Exception as e:
                print(f"Error generating AI support suggestion: {type(e).__name__}: {e}")
                ai_response = "Sorry, the AI assistant encountered an error while processing your request. A staff member will assist you."
        
        embed = discord.Embed(
            title=f"💬 {issue_title_display}", 
            color=int(self.bot.config['embed_color'], 16), 
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)

        if self.order_id:
            embed.add_field(name="Related Order", value=f"Order ID: `#{self.order_id}`\nProducts: {items_str}", inline=False)
        else:
            embed.add_field(name="Inquiry Type", value="General Question/Issue", inline=False)

        embed.add_field(name="Your Detailed Issue", value=self.issue_description.value, inline=False)
        embed.add_field(name="AI Assistant Suggestion", value=ai_response, inline=False)
        embed.set_footer(text="A staff member will review this and get back to you shortly.")
        
        await interaction.channel.send(f"Thank you, {interaction.user.mention}! We've received your information and are reviewing it.", embed=embed)
        await interaction.followup.send("Your issue has been submitted to the staff. They will assist you in this channel shortly.", ephemeral=True)

class SupportTicketSelect(discord.ui.Select):
    def __init__(self, bot, user_orders: dict): # user_orders should be a dict {order_id: order_data}
        self.bot = bot
        # Filter for delivered orders to ensure they've actually bought it for product-specific support
        valid_orders = {oid: o for oid, o in user_orders.items() if o.get('status') == 'Delivered'}

        options = []
        # Add a default option for general inquiry regardless of past orders
        options.append(discord.SelectOption(label="General Question / Other Issue", value="general_inquiry", emoji="❓", description="For issues not related to a specific past purchase."))

        # Add options for specific delivered orders
        for oid, order in valid_orders.items():
            products_in_order_desc = ", ".join(item.get('name', 'Unknown Product') for item in order.get('items', {}).values())
            options.append(
                discord.SelectOption(
                    label=f"Order #{oid}", 
                    value=oid, 
                    description=products_in_order_desc[:100] # Truncate description to 100 chars
                )
            )
        
        # Ensure total options don't exceed 25 (Discord limit for select menus)
        options = options[:25] 

        if len(options) == 1 and options[0].value == "general_inquiry":
            # If only general inquiry is available, make placeholder more specific
            super().__init__(placeholder="No specific purchases found, select general inquiry...", options=options, custom_id="support_order_select")
        else:
            super().__init__(placeholder="Select the purchase you need help with, or a general inquiry...", options=options, custom_id="support_order_select")

    async def callback(self, interaction: discord.Interaction):
        selected_value = self.values[0]

        if selected_value == "general_inquiry":
            await interaction.response.send_modal(SupportIssueModal(self.bot, order_id=None)) # No order_id for general inquiry
        else:
            # If it's a specific order, pass its ID to the modal
            await interaction.response.send_modal(SupportIssueModal(self.bot, order_id=selected_value))
        
        # Disable the select menu after use to prevent multiple submissions from same menu
        self.disabled = True
        await interaction.edit_original_response(view=self.view)

class SupportTicketView(discord.ui.View):
    def __init__(self, bot, user_orders: dict): # user_orders is a dict of {order_id: order_data}
        super().__init__(timeout=180) # View times out after 3 minutes of user inactivity
        self.custom_id = "persistent_support_ticket_view" # Custom ID for persistence
        self.clear_items() # Clear items to avoid duplicates on re-instantiation
        self.add_item(SupportTicketSelect(bot, user_orders))

# --- UI FOR STAFF CONTROLS ---

class StaffTicketView(discord.ui.View):
    def __init__(self, bot, ticket_creator: discord.User = None): # Default to None for persistence loading
        super().__init__(timeout=None) # Set timeout to None for persistence across restarts
        self.bot = bot
        # Store ticket_creator_id directly for persistence and lookup
        self.ticket_creator_id = ticket_creator.id if ticket_creator else None
        self.custom_id = "persistent_staff_ticket_view" # Custom ID for persistence
        
        self.clear_items() # Clear existing items to avoid duplicates if re-instantiated
        
        # Add buttons directly in the constructor using custom_ids for persistence
        # Ensure proper row assignments if needed for layout.
        self.add_item(StaffTicketView.ClaimButton(self.bot))
        self.add_item(StaffTicketView.HistoryButton()) 
        self.add_item(StaffTicketView.CloseButton())

    # Nested classes for buttons (to keep them logically grouped with the view)
    class ClaimButton(discord.ui.Button):
        def __init__(self, bot):
            super().__init__(label="Claim Ticket", style=discord.ButtonStyle.primary, emoji="🙋", custom_id="staff_claim_ticket")
            self.bot = bot

        async def callback(self, interaction: discord.Interaction):
            try:
                await interaction.response.defer(ephemeral=False) # Defer publicly for visible feedback
                
                ticket_state = self.bot.active_tickets.get(interaction.channel.id, {})
                
                if ticket_state.get('claimed_by'):
                    # Try to fetch the user if not in cache, for accurate mention
                    claimed_user = self.bot.get_user(ticket_state['claimed_by'])
                    if not claimed_user:
                        try:
                            claimed_user = await self.bot.fetch_user(ticket_state['claimed_by'])
                        except discord.NotFound:
                            claimed_user = None # User not found
                    
                    claimed_by_mention = claimed_user.mention if claimed_user else f"User ID: {ticket_state['claimed_by']}"
                    await interaction.followup.send(f"This ticket is already claimed by {claimed_by_mention}.", ephemeral=True)
                    return

                ticket_state['claimed_by'] = interaction.user.id # Store claiming staff's ID
                self.bot.active_tickets[interaction.channel.id] = ticket_state # Update active_tickets in memory
                
                claimed_embed = discord.Embed(
                    description=f"✅ Ticket claimed by {interaction.user.mention}", 
                    color=discord.Color.green(),
                    timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                
                # Recreate parent view to transition to StaffClaimedView state
                # Access the ticket_creator_id from the original StaffTicketView instance
                parent_view_instance = self.view 
                new_view = StaffClaimedView(self.bot, ticket_creator_id=parent_view_instance.ticket_creator_id) 
                
                # Edit the original staff controls message to show the new view (claimed state)
                await interaction.message.edit(embed=claimed_embed, view=new_view)
                await interaction.followup.send(f"{interaction.user.mention} has claimed this ticket.", ephemeral=False) # Send public message
            except Exception as e:
                print(f"Error in ClaimButton callback for ticket {interaction.channel.id}: {type(e).__name__}: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ An unexpected error occurred: {type(e).__name__}: {e}", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ An unexpected error occurred: {type(e).__name__}: {e}", ephemeral=True)


    class HistoryButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="User History", style=discord.ButtonStyle.secondary, emoji="📜", custom_id="staff_history")

        async def callback(self, interaction: discord.Interaction):
            try:
                await interaction.response.defer(ephemeral=True) # Defer ephemerally for privacy
                bot = interaction.client # Access bot via interaction.client
                
                # Get ticket creator from active_tickets for accuracy, as it's the actual customer
                ticket_state = bot.active_tickets.get(interaction.channel.id, {})
                ticket_creator_id = ticket_state.get("creator_id")
                
                if not ticket_creator_id:
                    await interaction.followup.send("❌ Could not determine the ticket creator's history. Creator ID not found in ticket state.", ephemeral=True)
                    return

                ticket_creator = None
                try:
                    ticket_creator = await bot.fetch_user(ticket_creator_id)
                except discord.NotFound:
                    await interaction.followup.send(f"❌ Ticket creator (ID: {ticket_creator_id}) not found on Discord. Cannot fetch history.", ephemeral=True)
                    return
                except Exception as e:
                    await interaction.followup.send(f"❌ Error fetching ticket creator for history: {type(e).__name__}: {e}", ephemeral=True)
                    return

                orders = await bot.load_json('orders') # Load orders data
                # Filter orders for the specific ticket creator. Ensure user_id in JSON is handled as string for consistent comparison.
                user_orders = {oid: o for oid, o in orders.items() if str(o.get('user_id')) == str(ticket_creator.id)}
                
                embed = discord.Embed(
                    title=f"Order History for {ticket_creator.display_name}",
                    color=int(bot.config['embed_color'], 16),
                    timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                embed.set_thumbnail(url=ticket_creator.display_avatar.url)

                if not user_orders:
                    embed.description = "This user has no past orders recorded."
                else:
                    description = []
                    # Sorts orders by timestamp, newest first, and gets the 5 most recent ones
                    # Use .get('timestamp') with a fallback to a very old date string for safe sorting
                    sorted_orders = sorted(user_orders.items(), key=lambda item: item[1].get('timestamp', '1970-01-01T00:00:00+00:00'), reverse=True)[:5] 
                    
                    for order_id, order in sorted_orders:
                        products_list = [item.get('name', 'Unknown Product') for item in order.get('items', {}).values()]
                        products_str = ", ".join(products_list) if products_list else "No items"
                        
                        order_time_str = order.get('timestamp')
                        order_date_display = "Date N/A"
                        if order_time_str:
                            try:
                                # Convert ISO format string to datetime object, then to Discord timestamp format
                                order_time = datetime.datetime.fromisoformat(order_time_str)
                                order_date_display = f"<t:{int(order_time.timestamp())}:D>" # Short date format
                            except ValueError:
                                print(f"Warning: Malformed timestamp for order {order_id}: {order_time_str}. Displaying as 'Date N/A'.")
                                pass 

                        description.append(
                            f"**Order `#{order_id}`** ({order_date_display})\n"
                            f"> **Status:** `{order.get('status', 'Unknown')}`\n"
                            f"> **Items:** {products_str}\n"
                        )
                    embed.description = "\n".join(description)
                
                await interaction.followup.send(embed=embed, ephemeral=True)
            except Exception as e:
                print(f"Error in HistoryButton callback for ticket {interaction.channel.id}: {type(e).__name__}: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ An unexpected error occurred: {type(e).__name__}: {e}", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ An unexpected error occurred: {type(e).__name__}: {e}", ephemeral=True)


    class CloseButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="Close", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="staff_close_ticket")

        async def callback(self, interaction: discord.Interaction):
            try:
                bot = interaction.client # Access bot via interaction.client
                # close_ticket_action handles its own deferral, so no need to defer here.
                await close_ticket_action(interaction, bot)
            except Exception as e:
                print(f"Error in CloseButton callback for ticket {interaction.channel.id}: {type(e).__name__}: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ An unexpected error occurred: {type(e).__name__}: {e}", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ An unexpected error occurred: {type(e).__name__}: {e}", ephemeral=True)


class StaffClaimedView(discord.ui.View):
    def __init__(self, bot, ticket_creator_id: int = None): # Store creator ID directly. Can be None for persistence load.
        super().__init__(timeout=None) # Set timeout to None for persistence across restarts
        self.bot = bot
        self.ticket_creator_id = ticket_creator_id # This is the original creator's ID
        self.custom_id = "persistent_staff_claimed_view" # Custom ID for persistence
        
        self.clear_items() # Clear existing items to avoid duplicates if re-instantiated
        
        # Add the unclaim button, history, and close buttons for consistency
        # Ensure UnclaimButton also knows the ticket_creator_id for re-instantiating StaffTicketView
        self.add_item(StaffClaimedView.UnclaimButton(self.bot, self.ticket_creator_id))
        self.add_item(StaffTicketView.HistoryButton()) # Re-use the existing HistoryButton
        self.add_item(StaffTicketView.CloseButton()) # Re-use the existing CloseButton

    # Nested class for Unclaim Button
    class UnclaimButton(discord.ui.Button):
        def __init__(self, bot, ticket_creator_id: int = None): # Can be None for persistence load
            super().__init__(label="Unclaim", style=discord.ButtonStyle.secondary, emoji="👋", custom_id="staff_unclaim_ticket")
            self.bot = bot
            self.ticket_creator_id = ticket_creator_id # This is the original creator's ID

        async def callback(self, interaction: discord.Interaction):
            try:
                await interaction.response.defer(ephemeral=False) # Defer publicly

                ticket_state = self.bot.active_tickets.get(interaction.channel.id, {})
                
                # Only the person who claimed it OR a bot owner can unclaim
                is_owner_check = interaction.user.id in self.bot.config.get('owner_ids', [])
                
                claimed_by_id = ticket_state.get('claimed_by')
                
                if claimed_by_id and claimed_by_id != interaction.user.id and not is_owner_check:
                    await interaction.followup.send("❌ You cannot unclaim a ticket that was claimed by someone else.", ephemeral=True)
                    return

                ticket_state.pop('claimed_by', None) # Remove claimed_by key from in-memory state
                self.bot.active_tickets[interaction.channel.id] = ticket_state # Update active_tickets
                
                unclaimed_embed = discord.Embed(
                    description="--- **Staff Controls** ---", 
                    color=int(self.bot.config['embed_color'], 16),
                    timestamp=datetime.datetime.now(datetime.timezone.utc)
                )
                
                # Re-create the original StaffTicketView (unclaimed state)
                # Fetch the actual ticket_creator User object for the view initialization
                ticket_creator_user = None
                if self.ticket_creator_id:
                    try:
                        ticket_creator_user = await self.bot.fetch_user(self.ticket_creator_id)
                    except discord.NotFound:
                        print(f"Ticket creator {self.ticket_creator_id} not found when unclaiming.")
                    except Exception as e:
                        print(f"Error fetching ticket creator {self.ticket_creator_id} for unclaiming: {type(e).__name__}: {e}")
                
                original_view = StaffTicketView(self.bot, ticket_creator=ticket_creator_user)
                
                # Edit the original staff controls message to revert to the unclaimed state view
                await interaction.message.edit(embed=unclaimed_embed, view=original_view)
                await interaction.followup.send(f"{interaction.user.mention} has unclaimed this ticket.", ephemeral=False)
            except Exception as e:
                print(f"Error in UnclaimButton callback for ticket {interaction.channel.id}: {type(e).__name__}: {e}")
                if not interaction.response.is_done():
                    await interaction.response.send_message(f"❌ An unexpected error occurred: {type(e).__name__}: {e}", ephemeral=True)
                else:
                    await interaction.followup.send(f"❌ An unexpected error occurred: {type(e).__name__}: {e}", ephemeral=True)

# --- MAIN COG ---
class TicketSystem(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Ensure the bot has the active_tickets dictionary for in-memory tracking
        if not hasattr(bot, 'active_tickets'):
            self.bot.active_tickets = {}

    async def create_ticket_thread(self, interaction: discord.Interaction, ticket_type_info: dict):
        thread_emoji = ticket_type_info.get('emoji', '🎟️')
        
        # Check if user already has an active ticket to prevent spam/multiple tickets
        # Iterate over a copy of items to allow modification of self.bot.active_tickets
        for thread_id, state in list(self.bot.active_tickets.items()): 
            if state.get('creator_id') == interaction.user.id and state.get('status') == 'Open': # Check if ticket is still open
                try:
                    existing_thread = self.bot.get_channel(thread_id)
                    if existing_thread: # If the channel object still exists in cache
                        # Attempt to fetch to ensure it's truly an active thread and accessible
                        await existing_thread.fetch() 
                        await interaction.followup.send(f"⚠️ You already have an active ticket: {existing_thread.mention}. Please use your existing ticket or close it before opening a new one.", ephemeral=True)
                        return
                    else: # Channel not in cache, might be old/deleted
                        self.bot.active_tickets.pop(thread_id, None) # Clean up stale entry
                        print(f"Cleaned up stale active ticket entry for deleted/inaccessible thread {thread_id} for user {interaction.user.id}.")
                except (discord.NotFound, discord.HTTPException): # Thread deleted or inaccessible, so clean up
                    self.bot.active_tickets.pop(thread_id, None) # Clean up stale entry
                    print(f"Cleaned up stale active ticket entry for deleted/inaccessible thread {thread_id} for user {interaction.user.id}.")
                except Exception as e:
                    print(f"Error checking existing ticket {thread_id} for user {interaction.user.id}: {type(e).__name__}: {e}")
        
        # Determine thread name prefix based on category
        thread_name_prefix = "🛒" if ticket_type_info.get('category') == "BUY" else "💬" if ticket_type_info.get('category') == "SUPPORT" else "💡"
        
        try:
            # Create a private thread (only visible to creator and staff roles initially)
            # Truncate username and ensure total length <= 100 characters for Discord's limit
            display_name = interaction.user.display_name
            # Max length for the item name part to ensure total thread name <= 100 characters
            # e.g., "🛒-username-ticket-type" or "💬-username-product-name"
            # Reserve some characters for the prefix and potential item name if added later
            
            # Initial thread name, without specific product name for buy tickets yet
            initial_thread_name = f"{thread_name_prefix}-{display_name}-ticket"
            initial_thread_name = initial_thread_name[:100].strip('-') # Ensure it doesn't exceed 100 and clean trailing hyphens

            thread = await interaction.channel.create_thread(
                name=initial_thread_name, 
                type=discord.ChannelType.private_thread, # Create a private thread for tickets
                reason=f"Ticket opened by {interaction.user.name} for {ticket_type_info.get('label', 'General Inquiry')}"
            )
            print(f"Created new private thread: {thread.name} (ID: {thread.id})")

        except discord.Forbidden:
            await interaction.followup.send(f"❌ I don't have permissions to create private threads in this channel. Please check my permissions (Manage Threads, Create Private Threads) or contact staff.", ephemeral=True)
            return
        except Exception as e:
            await interaction.followup.send(f"❌ An unexpected error occurred while creating your ticket thread: {type(e).__name__}: {e}", ephemeral=True)
            print(f"Error creating ticket thread for {interaction.user.id}: {type(e).__name__}: {e}")
            return
        
        await interaction.edit_original_response(content=f"✅ Your ticket has been created: {thread.mention}")
        await thread.add_user(interaction.user) # Add the user to the private thread so they can see it
        
        # Mention staff roles configured in config.json
        staff_mentions = ' '.join([f'<@&{rid}>' for rid in self.bot.config.get('staff_role_ids', [])])
        
        embed = discord.Embed(
            title=f"{thread_emoji} {ticket_type_info.get('label', 'New Ticket')}", 
            description=f"Welcome, {interaction.user.mention}! Please describe your needs below.", 
            color=int(self.bot.config['embed_color'], 16),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        embed.set_footer(text=f"Ticket opened by {interaction.user.display_name}")
        
        category = ticket_type_info.get('category')
        
        # Store comprehensive ticket state in memory
        self.bot.active_tickets[thread.id] = {
            "creator_id": interaction.user.id,
            "category": category, # Store category for logic like gift command
            "status": "Open", # Initial status
            "cart": {}, # For BUY tickets, initialized empty
            "discount": 0.0,
            "discount_reason": "No Discount",
            "order_id": None, # Will be set upon order confirmation
            "gift_recipient_id": None, # Will be set by /gift command
            "cart_message_id": None # Will store the ID of the ShoppingCartView message
        }

        if category == "BUY":
            products = await self.bot.load_json('products') # Load all products for the select menu
            # Pass products to init of view. ProductSelect will perform type conversion and filtering.
            view = ShoppingCartView(self.bot, products) 
            
            # Initial cart embed message
            cart_embed = discord.Embed(
                title="🛒 Your Shopping Cart", 
                description="Your cart is empty. Select a product from the dropdown to begin.", 
                color=int(self.bot.config['embed_color'], 16),
                timestamp=datetime.datetime.now(datetime.timezone.utc)
            )
            cart_embed.set_footer(text="Grand Total: ₹0.00")
            
            # Send the cart message and save its ID to ticket_state for easy updates
            cart_message = await thread.send(content=staff_mentions, embed=cart_embed, view=view)
            self.bot.active_tickets[thread.id]['cart_message_id'] = cart_message.id # Store message ID in active_tickets
            print(f"Buy ticket {thread.id} created for {interaction.user.id}. Cart message ID: {cart_message.id}")

        elif category == "SUPPORT":
            orders = await self.bot.load_json('orders') # Load user's order history
            # Only show delivered orders for product-specific support
            user_orders = {oid: o for oid, o in orders.items() if str(o.get('user_id')) == str(interaction.user.id) and o.get('status') == 'Delivered'}
            
            view = SupportTicketView(self.bot, user_orders) # Pass filtered orders to view
            embed.description = (
                f"Welcome, {interaction.user.mention}! "
                "Please select the order you need assistance with from the dropdown menu, "
                "or choose 'General Question / Other Issue' for non-purchase related help."
            )
            await thread.send(content=staff_mentions, embed=embed, view=view)
            print(f"Support ticket {thread.id} created for {interaction.user.id}.")

        elif category == "GENERAL": # Handle the new general category
            embed.description = (
                f"Welcome, {interaction.user.mention}! "
                "Please describe your general inquiry or issue using the button below. A staff member will be with you shortly."
            )
            await thread.send(content=staff_mentions, embed=embed)
            
            # Send a button that triggers the General SupportIssueModal
            general_inquiry_button_view = discord.ui.View(timeout=300) # Give 5 minutes for modal
            general_inquiry_button_view.add_item(
                discord.ui.Button(label="Describe My General Inquiry", style=discord.ButtonStyle.primary, custom_id="trigger_general_inquiry_modal")
            )
            
            # Add a callback for this specific button. This makes it cleaner than trying to auto-open modal.
            async def trigger_general_inquiry_modal_callback(btn_interaction: discord.Interaction):
                # Ensure this button is always responded to
                if not btn_interaction.response.is_done():
                    await btn_interaction.response.send_modal(SupportIssueModal(self.bot, order_id=None))
                else: # Fallback if already responded to (e.g., race condition)
                    await btn_interaction.followup.send_modal(SupportIssueModal(self.bot, order_id=None))

                # Disable the button after it's clicked once to prevent multiple modal pop-ups
                if btn_interaction.message and btn_interaction.message.components:
                    try:
                        btn_interaction.message.components[0].children[0].disabled = True
                        await btn_interaction.message.edit(view=btn_interaction.message.view)
                    except Exception as e:
                        print(f"Error disabling general inquiry button: {type(e).__name__}: {e}")

            general_inquiry_button_view.children[0].callback = trigger_general_inquiry_modal_callback
            
            await thread.send(content="Please click the button below to describe your general issue:", view=general_inquiry_button_view)
            print(f"General ticket {thread.id} created for {interaction.user.id}.")

        else: # Fallback for any other custom category not explicitly handled
            await thread.send(content=staff_mentions, embed=embed)
            print(f"Unknown category ticket {thread.id} created for {interaction.user.id}.")

        # Add the staff controls message at the end of the ticket
        staff_control_embed = discord.Embed(
            description="--- **Staff Controls** ---", 
            color=int(self.bot.config['embed_color'], 16),
            timestamp=datetime.datetime.now(datetime.timezone.utc)
        )
        # Pass the original ticket creator's User object to the StaffTicketView
        await thread.send(embed=staff_control_embed, view=StaffTicketView(self.bot, ticket_creator=interaction.user))
        print(f"Staff controls deployed for ticket {thread.id}.")

    @app_commands.command(name="gift", description="Purchase the items in your cart for another user.")
    @app_commands.describe(recipient="The user who will receive the gift.")
    async def gift(self, interaction: discord.Interaction, recipient: discord.Member):
        # This command should only work inside an active BUY ticket thread created by the bot
        if interaction.channel.id not in self.bot.active_tickets:
            await interaction.response.send_message("This command can only be used inside an active ticket created by the bot.", ephemeral=True)
            return
        
        ticket_state = self.bot.active_tickets.get(interaction.channel.id)
        if not ticket_state or ticket_state.get('category') != 'BUY': # Check if it's explicitly a BUY ticket
            await interaction.response.send_message("This command is only for 'Buy Products/Services' tickets.", ephemeral=True)
            return

        if recipient.bot:
            await interaction.response.send_message("You cannot gift to a bot. Please select a human user.", ephemeral=True)
            return
        if recipient.id == interaction.user.id:
            await interaction.response.send_message("You cannot gift to yourself. If you want to buy for yourself, just proceed with the order normally.", ephemeral=True)
            return

        ticket_state['gift_recipient_id'] = recipient.id # Store the recipient's ID in the ticket state
        self.bot.active_tickets[interaction.channel.id] = ticket_state # Update active_tickets in memory

        await interaction.response.send_message(f"✅ This order is now designated as a gift for {recipient.mention}! When the product is delivered, they will receive the delivery DM instead of you.", ephemeral=True)

async def setup(bot: commands.Bot):
    # Register the cog with the bot
    await bot.add_cog(TicketSystem(bot))

    # This part is handled in main.py's setup_hook for persistent views.
    # The actual instances are passed to bot.add_view in main.py.
    # The custom_ids are defined here in the classes themselves.
    # For reference, custom_ids used:
    # "transcript_instructions_view"
    # "persistent_shopping_cart_view"
    # "persistent_staff_ticket_view"
    # "persistent_staff_claimed_view"
    # "persistent_support_ticket_view"
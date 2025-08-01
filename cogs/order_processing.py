# cogs/order_processing.py
import discord
from discord.ext import commands, tasks
from discord import app_commands
import datetime
import secrets
import string
from utils.checks import is_owner, is_staff_or_owner

class ManualOrderModal(discord.ui.Modal, title="Create Manual Order"):
    def __init__(self, bot, user: discord.User):
        super().__init__()
        self.bot = bot
        self.user = user
        self.product_ids = discord.ui.TextInput(label="Product IDs (comma-separated)", placeholder="e.g., prod1,prod2", required=True)
        self.status = discord.ui.TextInput(label="Order Status", default="Delivered", placeholder="e.g., Delivered, Processing", required=True)
        self.payment_method = discord.ui.TextInput(label="Payment Method", default="Manual", placeholder="e.g., UPI, Gift", required=True)
        self.credentials = discord.ui.TextInput(label="Credentials/Notes (Optional)", style=discord.TextStyle.paragraph, required=False)
        self.add_item(self.product_ids)
        self.add_item(self.status)
        self.add_item(self.payment_method)
        self.add_item(self.credentials)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        all_products = await self.bot.load_json('products') # Using bot's load_json
        orders = await self.bot.load_json('orders') # Using bot's load_json
        counters = await self.bot.load_json('counters') # Using bot's load_json

        last_order_number = counters.get('last_order_number', 0)
        new_order_number = last_order_number + 1
        order_id = f"ORD{new_order_number:04d}"
        
        order_items = {}
        input_ids = [pid.strip() for pid in self.product_ids.value.split(',') if pid.strip()]
        
        warnings = []
        for pid in input_ids:
            if pid in all_products:
                product = all_products[pid]
                # Ensure price is handled if it's None or missing
                price = product.get('price')
                if price is None:
                    warnings.append(f"Product `{pid}` ({product['name']}) has no price defined. Setting to 0.")
                    price = 0
                order_items[pid] = {"name": product['name'], "price": price, "quantity": 1}
            else:
                warnings.append(f"Product ID `{pid}` not found and was skipped.")

        if not order_items:
            await interaction.followup.send("❌ No valid products were found among the IDs provided. Order not created.", ephemeral=True)
            return

        # Update counters only if order is valid
        counters['last_order_number'] = new_order_number
        await self.bot.save_json('counters', counters) # Using bot's save_json

        orders[order_id] = {
            "user_id": self.user.id,
            "items": order_items,
            "status": self.status.value.strip().title(),
            "discount": 0, # Manual orders typically don't have discount unless manually set
            "timestamp": interaction.created_at.isoformat(),
            "payment_method": self.payment_method.value.strip(),
            "notes": f"Manually created by {interaction.user.display_name}. Credentials: {self.credentials.value if self.credentials.value else 'N/A'}"
        }
        
        await self.bot.save_json('orders', orders) # Using bot's save_json
        
        response_msg = f"✅ Manual order `#{order_id}` successfully created for {self.user.mention}."
        if warnings:
            response_msg += "\n\n**Warnings:**\n" + "\n".join(warnings)
            
        embed = discord.Embed(title="✅ Manual Order Created", description=response_msg, color=int(self.bot.config['success_color'], 16))
        await interaction.followup.send(embed=embed, ephemeral=True)

class OrderProcessing(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        # Ensure the task starts only after the bot is ready
        self.check_renewals.start()

    def cog_unload(self):
        self.check_renewals.cancel()

    @app_commands.command(name="confirm_payment", description="[STAFF] Manually confirm a payment (e.g., for UPI).")
    @is_staff_or_owner()
    async def confirm_payment(self, interaction: discord.Interaction, order_id: str, method: str):
        await interaction.response.defer(ephemeral=True)
        orders = await self.bot.load_json('orders') # Using bot's load_json
        
        if order_id not in orders:
            await interaction.followup.send("❌ Order ID not found.", ephemeral=True)
            return
        
        order = orders[order_id]
        if order.get('status') == "Payment Received":
            await interaction.followup.send(f"⚠️ Payment for order `#{order_id}` is already marked as 'Payment Received'.", ephemeral=True)
            return
        if order.get('status') == "Delivered":
            await interaction.followup.send(f"⚠️ Order `#{order_id}` is already marked as 'Delivered'. Payment confirmation is not needed.", ephemeral=True)
            return

        orders[order_id]['status'] = "Payment Received"
        orders[order_id]['payment_method'] = method.strip()
        await self.bot.save_json('orders', orders) # Using bot's save_json
        
        await interaction.followup.send(f"✅ Payment for order `#{order_id}` confirmed with method `{method}`. Status updated to 'Payment Received'.", ephemeral=True)
        
        # Notify user in their ticket channel if one exists
        ticket_channel_id = orders[order_id].get('channel_id')
        if ticket_channel_id:
            try:
                if (ticket_channel := self.bot.get_channel(ticket_channel_id)):
                    await ticket_channel.send(f"✅ Payment for this order (`#{order_id}`) has been manually confirmed by {interaction.user.mention}. A staff member will proceed with delivery shortly.")
                else:
                    user = await self.bot.fetch_user(order['user_id'])
                    await user.send(f"✅ Payment for your order (`#{order_id}`) has been confirmed by {interaction.user.display_name}. Please wait for delivery in your ticket or DMs.")
            except discord.NotFound:
                print(f"Ticket channel {ticket_channel_id} not found for order {order_id}. User might have closed it.")
            except discord.Forbidden:
                user = await self.bot.fetch_user(order['user_id'])
                if user: await user.send(f"✅ Payment for your order (`#{order_id}`) has been confirmed by {interaction.user.display_name}. Please wait for delivery in your ticket or DMs.")
                print(f"Could not send message to ticket channel {ticket_channel_id} (Forbidden).")


    @app_commands.command(name="manual_order", description="[OWNER] Manually create an order for a user.")
    @is_owner()
    async def manual_order(self, interaction: discord.Interaction, user: discord.User):
        await interaction.response.send_modal(ManualOrderModal(self.bot, user))

    @app_commands.command(name="mark_delivered", description="[STAFF] Mark an order as delivered and log points.")
    @is_staff_or_owner()
    async def mark_delivered(self, interaction: discord.Interaction, order_id: str, credentials: str = "Your product is ready!"):
        await interaction.response.defer(ephemeral=True)
        orders = await self.bot.load_json('orders') # Using bot's load_json
        order = orders.get(order_id)
        if not order:
            await interaction.followup.send("Order not found.", ephemeral=True)
            return

        if order.get('status') == 'Delivered':
            await interaction.followup.send("⚠️ This order has already been marked as delivered.", ephemeral=True)
            return
            
        if order.get('status') == 'Pending Payment':
            await interaction.followup.send("⚠️ This order is still pending payment. Please confirm payment before marking as delivered.", ephemeral=True)
            return

        products_db = await self.bot.load_json('products') # Using bot's load_json
        for product_id, item_in_order in order.get('items', {}).items():
            if product_id in products_db:
                product_stock = products_db[product_id].get('stock', 0)
                if product_stock != -1: # If not infinite stock
                    quantity_in_order = item_in_order.get('quantity', 1)
                    products_db[product_id]['stock'] = max(0, product_stock - quantity_in_order) # Ensure stock doesn't go negative
        await self.bot.save_json('products', products_db) # Using bot's save_json

        users = await self.bot.load_json('users') # Using bot's load_json
        user_id_str = str(order['user_id'])
        if user_id_str not in users:
            users[user_id_str] = {'points': 0}
        
        # Ensure loyalty_program config exists and has points_per_order
        loyalty_config = self.bot.config.get('loyalty_program', {})
        points_to_add = loyalty_config.get('points_per_order', 1) # Default to 1 if not set
        
        users[user_id_str]['points'] += points_to_add
        new_balance = users[user_id_str]['points']
        await self.bot.save_json('users', users) # Using bot's save_json

        try:
            # Fetch member to get their roles if they are still in the guild
            customer_member = interaction.guild.get_member(order['user_id']) # Prefer get_member for cache
            if not customer_member: # If not in cache, try to fetch
                customer_member = await interaction.guild.fetch_member(order['user_id'])
                
            loyalty_cog = self.bot.get_cog("LoyaltyProgram")
            if customer_member and loyalty_cog:
                # _update_user_roles now requires the interaction to get guild context
                await loyalty_cog._update_user_roles(interaction, customer_member, new_balance)
            elif not customer_member:
                print(f"Could not fetch member {order['user_id']} for role update (user not in guild or discord.NotFound).")
            else: # loyalty_cog not found
                print("LoyaltyProgram cog not found, cannot update user roles.")

        except Exception as e:
            print(f"Could not update roles for user {order['user_id']}: {e}")

        if referral_info := order.get('referral_info'):
            try:
                discounts = await self.bot.load_json('discounts') # Using bot's load_json
                referrer_id = referral_info['referrer_id']
                referrer = await self.bot.fetch_user(referrer_id)
                
                affiliate_config = self.bot.config.get('loyalty_program', {}).get('affiliate_program', {})
                reward_amount = affiliate_config.get('referrer_reward_discount_inr')

                if reward_amount is None:
                    print(f"Affiliate reward not configured. Skipping reward for {referrer_id}.")
                else:
                    reward_code = f"REWARD-{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))}"
                    discounts[reward_code] = { "type": "redeem", "discount_inr": reward_amount, "used": False, "generated_for_referrer_id": referrer_id }
                    await self.bot.save_json('discounts', discounts) # Using bot's save_json
                    try:
                        await referrer.send(f"🎉 Thank you for referring a new customer! As a reward, here is a discount code for **₹{reward_amount:.2f}**: `{reward_code}`")
                    except discord.Forbidden:
                        print(f"Could not send referral reward DM to {referrer_id} (DMs disabled).")
            except Exception as e:
                print(f"Failed to send referral reward to {referrer_id}: {e}")
        
        log_channel_id = self.bot.config.get("points_log_channel_id")
        if log_channel_id:
            if (log_channel := self.bot.get_channel(log_channel_id)):
                customer = None
                try:
                    customer = await self.bot.fetch_user(order['user_id'])
                except discord.NotFound:
                    print(f"Customer {order['user_id']} not found for points log.")

                item_details_str = ", ".join([f"{products_db.get(pid, {}).get('emoji', '📦')} {item['name']}" for pid, item in order.get('items', {}).items()])
                payment_method = order.get('payment_method', 'N/A')
                payment_emojis = {"UPI": "<:ib_UPI:1395716106487861349> ", "LTC": "<:ib_ltc:1395711921940070471>", "USDT": "<:ib_usdt:1395711927828615303>", "BTC": "₿", "MANUAL": "✍️"} # Added BTC, capitalized Manual
                payment_emoji = payment_emojis.get(payment_method.upper(), "💳") # Use .upper() for robustness
                
                description = (
                    f"• **Order ID:** `#{order_id}`\n"
                    f"• **User:** {customer.mention if customer else f'User ID: {order["user_id"]}'}\n"
                    f"• **Product:** {item_details_str}\n"
                    f"• **Payment:** {payment_emoji} {payment_method}\n"
                    f"• **Earned:** `+{points_to_add} Point`\n"
                    f"• **Total Points:** `{new_balance:02d}`"
                )
                log_embed = discord.Embed(description=description, color=int(self.bot.config['success_color'], 16))
                log_embed.set_author(name="💠 Points Logged", icon_url=interaction.user.display_avatar.url)
                if customer:
                    log_embed.set_thumbnail(url=customer.display_avatar.url)
                await log_channel.send(embed=log_embed)
            else:
                print(f"Points log channel {log_channel_id} not found.")

        # --- MODIFIED: Send DM to gift recipient if applicable, otherwise to customer ---
        try:
            target_user_id = order.get('gift_recipient_id') or order['user_id']
            target_user = await self.bot.fetch_user(target_user_id)
            
            dm_embed = discord.Embed(title="✅ Your Product Has Arrived!", color=int(self.bot.config['success_color'], 16))
            if order.get('gift_recipient_id'):
                purchaser = await self.bot.fetch_user(order['user_id'])
                dm_embed.description=f"You have received a gift from {purchaser.mention}!"
            else:
                dm_embed.description=f"Your order `#{order_id}` is complete."
            
            # Add credentials/notes
            dm_embed.add_field(name="Product Information", value=f"||{credentials}||", inline=False)

            # Only show points info to the original buyer if not a gift
            if not order.get('gift_recipient_id'):
                dm_embed.set_footer(text=f"You earned +{points_to_add} Infinity Point! You now have {new_balance} points.")
            
            # Suggest review
            product_ids_for_review = [f"`{pid}`" for pid in order.get('items', {}).keys()]
            if product_ids_for_review:
                product_ids_str = ", ".join(product_ids_for_review[:3]) # Limit to 3 for readability
                if len(product_ids_for_review) > 3:
                    product_ids_str += f" and more ({len(product_ids_for_review) - 3} others)"

                dm_embed.add_field(
                    name="Leave a Review!",
                    value=f"Enjoying your purchase? Let us know by using the `/review` command with one of your Product IDs: {product_ids_str}",
                    inline=False
                )

            await target_user.send(embed=dm_embed)
        except discord.Forbidden:
            # User has DMs disabled
            await interaction.followup.send(f"⚠️ Could not DM the customer (or gift recipient) for order `#{order_id}`. Their DMs might be closed.", ephemeral=True)
            print(f"Could not DM user {target_user_id} for order {order_id} delivery (DMs disabled).")
        except discord.NotFound:
            await interaction.followup.send(f"⚠️ Could not find the customer (or gift recipient) for order `#{order_id}` to DM.", ephemeral=True)
            print(f"Could not find user {target_user_id} for order {order_id} delivery.")
        except Exception as e:
            await interaction.followup.send(f"❌ An error occurred while attempting to DM delivery details for order `#{order_id}`: {e}", ephemeral=True)
            print(f"Error sending delivery DM for order {order_id}: {e}")


        order['status'] = 'Delivered'
        await self.bot.save_json('orders', orders) # Using bot's save_json
        await interaction.followup.send(f"✅ Order `#{order_id}` marked delivered. All systems (stock, points, roles, referrals) have been updated.", ephemeral=True)

    @app_commands.command(name="dashboard", description="[STAFF] View store sales and product statistics.")
    @is_staff_or_owner()
    async def dashboard(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        orders = await self.bot.load_json('orders') # Using bot's load_json
        
        total_revenue = 0; orders_today = 0; revenue_today = 0; product_sales = {}
        now = datetime.datetime.now(datetime.timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        delivered_orders = [o for o in orders.values() if o.get('status') == 'Delivered']

        for order in delivered_orders:
            # Calculate order total, applying discount
            order_total = sum(i.get('price', 0) * i.get('quantity', 0) for i in order['items'].values()) - order.get('discount', 0)
            order_total = max(0, order_total) # Ensure total is not negative

            total_revenue += order_total
            for item_key, item_data in order['items'].items(): # Iterate through item_key to get pid if needed, but item_data is sufficient
                product_sales[item_data['name']] = product_sales.get(item_data['name'], 0) + item_data.get('quantity', 1) # Use item_data['name'] for product sales count
            
            # Handle potential ValueError if timestamp is malformed
            order_time_str = order.get('timestamp')
            order_time = None
            if order_time_str:
                try:
                    order_time = datetime.datetime.fromisoformat(order_time_str)
                except ValueError:
                    print(f"Warning: Malformed timestamp for order {order_id}: {order_time_str}. Skipping for 'today' calculation.")
                    continue # Skip this order for today's calculation if timestamp is bad

            if order_time and order_time >= today_start:
                orders_today += 1
                revenue_today += order_total
        
        top_products = sorted(product_sales.items(), key=lambda item: item[1], reverse=True)
        top_products_str = "\n".join([f"**{name}**: `{count}` sold" for name, count in top_products[:5]])
        if not top_products_str: top_products_str = "No product sales yet."

        embed = discord.Embed(title="📊 Sales Dashboard", color=int(self.bot.config['embed_color'], 16), timestamp=now)
        embed.add_field(name="💰 Total Revenue (All-Time)", value=f"₹{total_revenue:.2f}", inline=True)
        embed.add_field(name="📈 Revenue Today", value=f"₹{revenue_today:.2f}", inline=True)
        embed.add_field(name="📦 Orders Today", value=str(orders_today), inline=True)
        embed.add_field(name="🏆 Top 5 Selling Products", value=top_products_str, inline=False)
        embed.set_footer(text="Based on delivered orders only")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="order_info", description="[STAFF] Get the full details of a specific order.")
    @is_staff_or_owner()
    async def order_info(self, interaction: discord.Interaction, order_id: str):
        await interaction.response.defer(ephemeral=True)
        orders = await self.bot.load_json('orders') # Using bot's load_json
        order = orders.get(order_id)

        if not order:
            await interaction.followup.send("❌ Order ID not found.", ephemeral=True); return
            
        customer_mention = f"User ID: {order['user_id']}"
        customer_avatar_url = None
        try:
            customer = await self.bot.fetch_user(order['user_id'])
            customer_mention = customer.mention
            customer_avatar_url = customer.display_avatar.url
        except discord.NotFound:
            print(f"Customer {order['user_id']} not found for order info display.")
        except Exception as e:
            print(f"Error fetching customer for order info: {e}")

        embed = discord.Embed(title=f"Details for Order `#{order_id}`", color=int(self.bot.config['embed_color'], 16))
        embed.set_author(name=f"Order for {customer_mention}", icon_url=customer_avatar_url)
        
        order_time_str = order.get('timestamp')
        order_time = None
        if order_time_str:
            try:
                order_time = datetime.datetime.fromisoformat(order_time_str)
            except ValueError:
                print(f"Warning: Invalid timestamp format for order {order_id}: {order_time_str}")
        
        embed.add_field(name="Status", value=order.get('status', 'N/A'), inline=True)
        embed.add_field(name="Payment Method", value=order.get('payment_method', 'N/A'), inline=True)
        embed.add_field(name="Order Date", value=f"<t:{int(order_time.timestamp())}:F>" if order_time else "N/A", inline=True) # Full timestamp
        
        items_str = "No items found."
        if order.get('items'):
            items_str_list = []
            for item_id, item_data in order['items'].items():
                item_name = item_data.get('name', 'Unknown Product')
                item_quantity = item_data.get('quantity', 1)
                item_price = item_data.get('price', 0)
                items_str_list.append(f"• {item_name} `x{item_quantity}` @ ₹{item_price:.2f}")
            items_str = "\n".join(items_str_list)

        embed.add_field(name="Items Purchased", value=items_str, inline=False)
        
        total_price = sum(i.get('price', 0) * i.get('quantity', 0) for i in order['items'].values())
        discount = order.get('discount', 0)
        final_price = max(0, total_price - discount) # Ensure final price is not negative

        price_details = f"Subtotal: `₹{total_price:.2f}`"
        if discount > 0:
            discount_reason = order.get('discount_reason', 'Discount')
            price_details += f"\n{discount_reason}: `-₹{discount:.2f}`\n**Total: `₹{final_price:.2f}`**"
        else:
            price_details += f"\n**Total: `₹{final_price:.2f}`**"

        embed.add_field(name="Pricing", value=price_details, inline=False)
        
        if referral := order.get('referral_info'):
            embed.add_field(name="Referral Info", value=f"Code `{referral.get('code', 'N/A')}` used (Referrer ID: {referral.get('referrer_id', 'N/A')}).", inline=True)
        
        if order.get('gift_recipient_id'):
            try:
                gift_recipient = await self.bot.fetch_user(order['gift_recipient_id'])
                embed.add_field(name="Gift Recipient", value=gift_recipient.mention, inline=True)
            except discord.NotFound:
                embed.add_field(name="Gift Recipient", value=f"User ID: {order['gift_recipient_id']} (Not Found)", inline=True)


        notes = order.get('notes')
        if notes:
            embed.add_field(name="Notes", value=notes, inline=False)

        await interaction.followup.send(embed=embed, ephemeral=True)

    # ---
    ### New: Background task for renewal reminders
    # ---
    @tasks.loop(hours=24)
    async def check_renewals(self):
        await self.bot.wait_until_ready()
        print("Running daily renewal check...")
        orders = await self.bot.load_json('orders') # Using bot's load_json
        products = await self.bot.load_json('products') # Using bot's load_json
        
        renewal_alerts_channel_id = self.bot.config.get('renewal_alerts_channel_id')
        alerts_channel = None
        if renewal_alerts_channel_id:
            alerts_channel = self.bot.get_channel(renewal_alerts_channel_id)
            if not alerts_channel:
                print(f"Renewal alerts channel {renewal_alerts_channel_id} not found.")

        now = datetime.datetime.now(datetime.timezone.utc)

        for order_id, order in orders.items():
            if order.get('status') != 'Delivered': continue # Only check delivered orders
            
            delivery_date_str = order.get('timestamp')
            if not delivery_date_str:
                print(f"Order {order_id} has no timestamp, skipping renewal check.")
                continue
            
            try:
                delivery_date = datetime.datetime.fromisoformat(delivery_date_str)
            except ValueError:
                print(f"Malformed timestamp for order {order_id}: {delivery_date_str}, skipping renewal check.")
                continue

            for pid, item in order.get('items', {}).items():
                product_info = products.get(pid)
                if not product_info:
                    print(f"Product {pid} for order {order_id} not found in products database, skipping renewal check.")
                    continue

                renewal_days = product_info.get('renewal_period_days')
                if not renewal_days or not isinstance(renewal_days, int) or renewal_days <= 0:
                    continue # Skip if no valid renewal period

                expiry_date = delivery_date + datetime.timedelta(days=renewal_days)
                days_left = (expiry_date - now).days

                # Send reminder 3 days before expiry
                if days_left == 3: # Can be adjusted to a configurable value
                    try:
                        user = await self.bot.fetch_user(order['user_id'])
                        
                        # Only send if the order isn't already handled by a gift recipient
                        # This avoids sending renewal to the original buyer if it was a gift
                        if order.get('gift_recipient_id') and order['gift_recipient_id'] != user.id:
                            print(f"Skipping renewal reminder for original buyer {user.id} as it was a gift for {order['gift_recipient_id']}.")
                            continue

                        # Check if a reminder has already been sent for this period/order to prevent duplicates
                        # This would require a new 'reminded_for_renewal' flag in the order
                        # For simplicity, for now, we'll send it once.
                        
                        await user.send(f"🔔 **Subscription Reminder** 🔔\nYour product **{item['name']}** from order `#{order_id}` is expiring in 3 days ({expiry_date.strftime('%Y-%m-%d')}). Open a ticket to renew and avoid interruption!")
                        if alerts_channel:
                            await alerts_channel.send(f"Sent renewal reminder to {user.mention} for product **{item['name']}** (Order `#{order_id}`).")
                        print(f"Sent renewal reminder to {user.display_name} for product {item['name']} (Order {order_id}).")
                    except discord.Forbidden:
                        print(f"Failed to send renewal reminder to {order['user_id']}: DMs disabled.")
                        if alerts_channel:
                            alert_user = await self.bot.fetch_user(order['user_id']) # To get mention if possible
                            if alert_user:
                                await alerts_channel.send(f"⚠️ Could not send renewal reminder DM to {alert_user.mention} for product **{item['name']}** (Order `#{order_id}`). DMs are disabled.")
                            else:
                                await alerts_channel.send(f"⚠️ Could not send renewal reminder DM to user ID {order['user_id']} for product **{item['name']}** (Order `#{order_id}`). DMs are disabled.")
                    except discord.NotFound:
                        print(f"Failed to send renewal reminder: User {order['user_id']} not found.")
                    except Exception as e:
                        print(f"Failed to send renewal reminder for order {order_id} to user {order['user_id']}: {e}")

async def setup(bot: commands.Cog):
    await bot.add_cog(OrderProcessing(bot))
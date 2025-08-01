# cogs/payment_gateway.py
import discord
from discord.ext import commands
import requests
import qrcode
import io
import asyncio # Import asyncio for a more robust dummy user object if needed

class PaymentView(discord.ui.View):
    def __init__(self, bot, order_id, user: discord.User, cart, discount):
        super().__init__(timeout=900) # View times out after 15 minutes
        self.bot = bot
        self.order_id = order_id
        self.user = user # Store the actual user for refreshing rates
        self.cart = cart
        self.discount = discount

    @discord.ui.button(label="Refresh Crypto Rates", style=discord.ButtonStyle.secondary, emoji="🔄")
    async def refresh_rates(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True) # Defer ephemeral to avoid clutter
        payment_cog = self.bot.get_cog("PaymentGateway")
        if not payment_cog:
            await interaction.followup.send("An error occurred: Payment gateway cog not found. Please contact staff.", ephemeral=True)
            return
        
        # Regenerate content using the stored order details
        new_embed, new_file = await payment_cog.generate_payment_embed_content(
            self.order_id, self.user, self.cart, self.discount
        )
        
        # Prepare files list for editing message
        files_to_send = [new_file] if new_file else []
        
        # Edit the original message (the one with the buttons)
        try:
            await interaction.message.edit(embed=new_embed, attachments=files_to_send, view=self)
            await interaction.followup.send("✅ Payment rates refreshed.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to refresh rates or update message: {e}", ephemeral=True)


class PaymentGateway(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Ensure 'btc_address' is included if you intend to support it.
        # This map defines supported cryptos and their CoinGecko IDs/symbols.
        self.coin_map = {
            "ltc_address": {"id": "litecoin", "name": "Litecoin (LTC)", "symbol": "LTC"},
            "usdt_trc20_address": {"id": "tether", "name": "Tether (USDT TRC20)", "symbol": "USDT"},
            "btc_address": {"id": "bitcoin", "name": "Bitcoin (BTC)", "symbol": "BTC"},
            # Add other cryptos here if supported in config
            # "eth_address": {"id": "ethereum", "name": "Ethereum (ETH)", "symbol": "ETH"},
        }

    async def get_coingecko_rates(self):
        # Only fetch rates for coins that have an address configured in bot.config['payment_methods']
        configured_coin_ids = [
            details["id"] for key, details in self.coin_map.items() 
            if key in self.bot.config.get('payment_methods', {}) and self.bot.config['payment_methods'].get(key)
        ]
        
        if not configured_coin_ids:
            print("No crypto addresses configured for CoinGecko lookup.")
            return {}

        api_url = f"https://api.coingecko.com/api/v3/simple/price?ids={','.join(configured_coin_ids)}&vs_currencies=inr"
        try:
            r = requests.get(api_url, timeout=10) # Add timeout for robustness
            r.raise_for_status() # Raise an HTTPError for bad responses (4xx or 5xx)
            return r.json()
        except requests.exceptions.Timeout:
            print("CoinGecko Error: Request timed out.")
            return None
        except requests.exceptions.RequestException as e:
            print(f"CoinGecko Error: Could not fetch crypto rates. {e}")
            return None
        except Exception as e:
            print(f"An unexpected error occurred while fetching CoinGecko rates: {e}")
            return None

    async def generate_payment_embed_content(self, order_id: str, user: discord.User, cart: dict, discount: float = 0.0):
        total_inr = sum(item.get('price', 0) * item.get('quantity', 0) for item in cart.values()) - discount
        # Ensure total_inr is not negative
        total_inr = max(0, total_inr)

        rates = await self.get_coingecko_rates()
        if rates is None: # Handle cases where API call itself failed
            return discord.Embed(title="⚠️ Payment Service Temporarily Unavailable", description="Could not fetch live crypto rates. Please try again later or contact staff.", color=int(self.bot.config['error_color'], 16)), None
        if not rates: # Handle cases where no configured coins could fetch rates
            return discord.Embed(title="⚠️ Crypto Payments Not Available", description="No supported crypto payment methods are configured or active.", color=int(self.bot.config['error_color'], 16)), None

        pm = self.bot.config.get('payment_methods', {})
        
        qr_file = None
        # Check if UPI is configured and generate QR
        if pm.get('upi_id'):
            # Ensure the amount is formatted correctly for UPI (2 decimal places)
            upi_uri = f"upi://pay?pa={pm['upi_id']}&pn=YourStore&am={total_inr:.2f}&cu=INR&tn=Order-{order_id}"
            try:
                qr = qrcode.make(upi_uri)
                img_arr = io.BytesIO(); qr.save(img_arr, format='PNG'); img_arr.seek(0)
                qr_file = discord.File(fp=img_arr, filename="upi_qr.png")
            except Exception as e:
                print(f"Error generating UPI QR code: {e}")
                # Don't fail the entire embed generation, just skip QR
                qr_file = None

        embed = discord.Embed(title="✅ Order Invoice", description=f"Please pay **₹{total_inr:.2f}** for Order `{order_id}`", color=int(self.bot.config['success_color'], 16))
        embed.set_author(name=f"Invoice for {user.display_name}", icon_url=user.display_avatar.url)
        
        if pm.get('upi_id'):
            embed.add_field(name="📱 UPI Payment", value=f"**ID:** `{pm['upi_id']}`\n**Note:** `Order-{order_id}`", inline=False)
        
        # Add crypto payment options dynamically
        for key, details in self.coin_map.items():
            address = pm.get(key)
            if address and (coin_rate_info := rates.get(details["id"])) and (inr_rate := coin_rate_info.get("inr", 0)) > 0:
                crypto_amount = total_inr / inr_rate
                embed.add_field(
                    name=f"<{details['symbol'].upper()}> {details['name']}", # Use symbol directly
                    value=f"Send **{crypto_amount:.8f} {details['symbol']}** to the address below:\n`{address}`",
                    inline=False
                )
        
        if qr_file: 
            embed.set_image(url="attachment://upi_qr.png") # Link to the attached QR code image
        
        embed.set_footer(text="After paying with Crypto, use /verify_payment in this ticket to confirm. Rates refresh every 5 minutes.")
        embed.set_thumbnail(url=self.bot.user.display_avatar.url) # Use bot's avatar as thumbnail

        return embed, qr_file

    async def generate_payment_embed(self, order_id: str, user: discord.User, cart: dict, discount: float = 0.0):
        embed, file = await self.generate_payment_embed_content(order_id, user, cart, discount)
        # Create the view, passing the necessary info to its init
        view = PaymentView(self.bot, order_id, user, cart, discount)
        return embed, file, view
        
async def setup(bot: commands.Cog):
    await bot.add_cog(PaymentGateway(bot))
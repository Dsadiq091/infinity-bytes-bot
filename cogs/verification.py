# cogs/verification.py
import discord
from discord.ext import commands
from discord import app_commands
import requests

class Verification(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Dynamically create choices based on configured wallet addresses
        self.supported_networks = []
        for key in bot.config['payment_methods']:
            if key == "ltc_address":
                self.supported_networks.append(app_commands.Choice(name="Litecoin (LTC)", value="LTC"))
            elif key == "btc_address":
                self.supported_networks.append(app_commands.Choice(name="Bitcoin (BTC)", value="BTC"))
            elif key == "doge_address":
                self.supported_networks.append(app_commands.Choice(name="Dogecoin (DOGE)", value="DOGE"))

    @app_commands.command(name="verify_payment", description="Verify your crypto transaction using its TXID.")
    async def verify_payment(self, interaction: discord.Interaction, order_id: str, transaction_id: str, network: str):
        """Verifies a crypto payment by checking the transaction ID on the SoChain API."""
        await interaction.response.defer(ephemeral=True)
        
        orders = await self.bot.load_json('orders')
        order = orders.get(order_id)
        
        # --- Initial Order Validation ---
        if not order or order['user_id'] != interaction.user.id or order['status'] != "Pending Payment":
            await interaction.followup.send("❌ **Invalid Order:** This order ID is not valid, doesn't belong to you, or is not pending payment.", ephemeral=True)
            return

        # --- SoChain API Call ---
        api_url = f"https://sochain.com/api/v2/get_tx/{network}/{transaction_id}"
        try:
            response = requests.get(api_url)
            response.raise_for_status()
            tx_data = response.json().get('data', {})
        except requests.exceptions.RequestException:
            await interaction.followup.send("❌ **Invalid TXID:** The transaction ID could not be found. Please check the ID and the selected network.", ephemeral=True)
            return

        # --- Confirmation Check ---
        confirmations = tx_data.get('confirmations', 0)
        required_confirmations = 3
        if confirmations < required_confirmations:
            await interaction.followup.send(f"⏳ **Pending Confirmation:** Your transaction has been found with `{confirmations}` of `{required_confirmations}` required confirmations. Please wait a few more minutes and try again.", ephemeral=True)
            return
        
        # --- Amount Verification Check ---
        our_address = self.bot.config['payment_methods'].get(f"{network.lower()}_address")
        total_paid = sum(float(o['value']) for o in tx_data.get('outputs', []) if o.get('address') == our_address)
        
        rates_cog = self.bot.get_cog("PaymentGateway")
        rates = await rates_cog.get_coingecko_rates()
        if not rates:
            await interaction.followup.send("⚠️ **Service Error:** Could not fetch live crypto prices to verify the amount. Please ask a staff member for help.", ephemeral=True)
            return
            
        total_inr = sum(i['price'] * i['quantity'] for i in order['items'].values()) - order.get('discount', 0)
        expected_crypto = total_inr / rates[f"{network}INR"]
        
        # Allow for a small margin of error (e.g., 1%) for price fluctuations
        if total_paid >= expected_crypto * 0.99:
            # --- Success ---
            orders[order_id]['status'] = 'Payment Received'
            orders[order_id]['payment_method'] = network # Save the payment method
            await self.bot.save_json('orders', orders)
            
            await interaction.followup.send("✅ **Payment Verified!** A staff member will process your order shortly.", ephemeral=True)
            
            ticket_channel = self.bot.get_channel(order.get('channel_id')) or interaction.channel
            if ticket_channel:
                await ticket_channel.send(f"✅ Payment for Order `{order_id}` has been automatically verified by {interaction.user.mention}.")
        else:
            await interaction.followup.send(f"❌ **Amount Mismatch:** The amount sent (`{total_paid:.8f} {network}`) does not match the required amount (`~{expected_crypto:.8f} {network}`). Please contact staff.", ephemeral=True)

    @verify_payment.autocomplete('network')
    async def network_autocomplete(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        """Dynamically provides the network choices."""
        return [choice for choice in self.supported_networks if current.lower() in choice.name.lower()]

async def setup(bot: commands.Bot):
    await bot.add_cog(Verification(bot))
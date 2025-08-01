# cogs/setup.py
import discord
from discord.ext import commands
from discord import app_commands
import json
from utils.checks import is_owner # Import our new owner check

# Modified to use bot's save_json method
async def update_config_value(bot: commands.Bot, key: str, value):
    """Helper function to safely update a specific key in the bot's config and save it."""
    # Ensure the config is mutable if it's not already
    if not isinstance(bot.config, dict):
        bot.config = {} # Reinitialize if it's not a dict
    
    bot.config[key] = value
    # Save the entire config object
    await bot.save_json('config', bot.config)
    print(f"Config updated: {key} = {value}")


# --- MODIFIED: Ticket Panel View now uses Buttons instead of a Dropdown ---
class TicketButton(discord.ui.Button):
    """A custom button for a specific ticket category."""
    def __init__(self, bot: commands.Bot, ticket_option: dict):
        super().__init__(
            label=ticket_option.get("label", "Ticket"),
            emoji=ticket_option.get("emoji"),
            style=discord.ButtonStyle.secondary,
            custom_id=f"ticket_cat_{ticket_option.get('category', 'general').lower()}" # Ensure custom_id is lowercase and safe
        )
        self.bot = bot
        self.ticket_type_info = ticket_option

    async def callback(self, interaction: discord.Interaction):
        # Defer immediately to prevent "Interaction Failed"
        await interaction.response.defer(ephemeral=True, thinking=True)
        
        ticket_cog = self.bot.get_cog("TicketSystem")
        if ticket_cog:
            # Call the function in ticket_system.py to create the thread
            await ticket_cog.create_ticket_thread(interaction, self.ticket_type_info)
        else:
            await interaction.followup.send("Ticket system is currently unavailable. Please notify a bot administrator.", ephemeral=True)


class TicketPanelView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=None) # Set timeout to None for persistence
        self.bot = bot
        self.custom_id = "persistent_ticket_panel_view" # Add a custom_id for persistence
        
        # Dynamically create buttons from the bot's config
        ticket_options = self.bot.config.get('ticket_options', [])
        
        # Clear existing items if this view is re-instantiated, to avoid duplicates
        self.clear_items() 

        if not ticket_options:
            # Add a placeholder button or message if no options are configured
            self.add_item(discord.ui.Button(label="No Ticket Types Configured", style=discord.ButtonStyle.red, disabled=True))
            print("Warning: No ticket_options found in config.json. Ticket panel will show a disabled button.")
        else:
            for opt in ticket_options:
                self.add_item(TicketButton(bot=self.bot, ticket_option=opt))

class Setup(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="set_channels", description="[OWNER] Configure all essential bot channels.")
    @is_owner()
    async def set_channels(self, interaction: discord.Interaction, 
                           panel_channel: discord.TextChannel, 
                           transcripts_channel: discord.TextChannel,
                           points_log_channel: discord.TextChannel,
                           review_channel: discord.TextChannel = None, # Optional: Allow setting review channel
                           renewal_alerts_channel: discord.TextChannel = None): # Optional: Allow setting renewal alerts channel
        await interaction.response.defer(ephemeral=True)

        await update_config_value(self.bot, "ticket_panel_channel_id", panel_channel.id)
        await update_config_value(self.bot, "ticket_transcripts_channel_id", transcripts_channel.id)
        await update_config_value(self.bot, "points_log_channel_id", points_log_channel.id)
        
        description = (
            f"**Ticket Panel Channel:** {panel_channel.mention}\n"
            f"**Transcripts Channel:** {transcripts_channel.mention}\n"
            f"**Points Log Channel:** {points_log_channel.mention}"
        )

        if review_channel:
            await update_config_value(self.bot, "review_channel_id", review_channel.id)
            description += f"\n**Review Channel:** {review_channel.mention}"
        else:
            await update_config_value(self.bot, "review_channel_id", None) # Clear if not provided

        if renewal_alerts_channel:
            await update_config_value(self.bot, "renewal_alerts_channel_id", renewal_alerts_channel.id)
            description += f"\n**Renewal Alerts Channel:** {renewal_alerts_channel.mention}"
        else:
            await update_config_value(self.bot, "renewal_alerts_channel_id", None) # Clear if not provided

        embed = discord.Embed(title="✅ Channels Configured", description=description, color=int(self.bot.config['success_color'], 16))
        await interaction.followup.send(embed=embed)
        
        # After updating config, re-instantiate and add the TicketPanelView to bot if it exists
        # This will refresh its buttons with the latest config.
        # This is important if you later add new ticket types via config.json and want them to appear.
        try:
            # Remove old view instance if it exists and then add new one
            existing_view = discord.utils.get(self.bot.persistent_views, custom_id="persistent_ticket_panel_view")
            if existing_view:
                self.bot.remove_view(existing_view)
            
            new_panel_view = TicketPanelView(bot=self.bot)
            self.bot.add_view(new_panel_view)
            print("Refreshed TicketPanelView after channel configuration.")
        except Exception as e:
            print(f"Error refreshing TicketPanelView after channel configuration: {e}")


    @app_commands.command(name="add_staff_role", description="[OWNER] Add a role that can manage tickets.")
    @is_owner()
    async def add_staff_role(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        # Ensure staff_role_ids is a list in config
        current_staff_roles = self.bot.config.get('staff_role_ids', [])
        if not isinstance(current_staff_roles, list):
            current_staff_roles = [] # Reset if not a list
        
        if role.id not in current_staff_roles:
            current_staff_roles.append(role.id)
            await update_config_value(self.bot, 'staff_role_ids', current_staff_roles)
            await interaction.followup.send(f"✅ Role {role.mention} has been added to the staff list.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Role {role.mention} is already a staff role.", ephemeral=True)

    @app_commands.command(name="remove_staff_role", description="[OWNER] Remove a staff role.")
    @is_owner()
    async def remove_staff_role(self, interaction: discord.Interaction, role: discord.Role):
        await interaction.response.defer(ephemeral=True)
        current_staff_roles = self.bot.config.get('staff_role_ids', [])
        if not isinstance(current_staff_roles, list):
            current_staff_roles = [] # Cannot remove if it's not a list

        if role.id in current_staff_roles:
            current_staff_roles.remove(role.id)
            await update_config_value(self.bot, 'staff_role_ids', current_staff_roles)
            await interaction.followup.send(f"✅ Role {role.mention} has been removed from the staff list.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Role {role.mention} is not in the staff list.", ephemeral=True)

    @app_commands.command(name="deploy_ticket_panel", description="[OWNER] Deploys the ticket panel to the configured channel.")
    @is_owner()
    async def deploy_ticket_panel(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        channel_id = self.bot.config.get("ticket_panel_channel_id")
        
        if not channel_id:
            await interaction.followup.send("Error: Ticket panel channel not configured. Please run `/set_channels` first.", ephemeral=True)
            return

        channel = self.bot.get_channel(channel_id)
        if not channel or not isinstance(channel, discord.TextChannel):
            await interaction.followup.send(f"Error: Configured ticket panel channel (ID: {channel_id}) not found or is not a text channel. Please check the ID or run `/set_channels` again.", ephemeral=True)
            return

        embed = discord.Embed(title="Support & Shopping Tickets", description="Welcome! Select an option below to open a ticket.", color=int(self.bot.config['embed_color'], 16))
        
        ticket_panel_image_url = self.bot.config.get("ticket_panel_image_url")
        if ticket_panel_image_url:
            embed.set_image(url=ticket_panel_image_url) # Use image on the embed

        # Re-initialize the view to ensure it has the latest ticket_options from config
        view = TicketPanelView(bot=self.bot)
        
        try:
            await channel.send(embed=embed, view=view)
            await interaction.followup.send(f"✅ Ticket panel deployed successfully in {channel.mention}!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(f"❌ I don't have permission to send messages in {channel.mention}. Please check my permissions.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ An unexpected error occurred while deploying the ticket panel: {e}", ephemeral=True)
            print(f"Error deploying ticket panel: {e}")

async def setup(bot: commands.Bot):
    await bot.add_cog(Setup(bot))
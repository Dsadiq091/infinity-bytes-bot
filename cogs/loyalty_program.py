# cogs/loyalty_program.py
import discord
from discord.ext import commands
from discord import app_commands
from utils.checks import is_owner

import secrets
import string

# --- UI for redeeming points ---
class RedeemSelect(discord.ui.Select):
    def __init__(self, bot, affordable_rewards: list):
        self.bot = bot
        options = [
            discord.SelectOption(
                label=f"Redeem {reward['points']} points",
                value=str(reward['points']),
                description=f"Get a discount of ₹{reward['discount_inr']:.2f}" # Format discount
            ) for reward in affordable_rewards
        ]
        if not options:
            options.append(discord.SelectOption(label="You cannot afford any rewards yet.", value="disabled", emoji="❌"))
        super().__init__(placeholder="Choose a reward to redeem...", options=options, custom_id="redeem_reward_select") # Added custom_id

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "disabled":
            await interaction.response.send_message("No reward selected or no rewards available.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)
        
        selected_points = int(self.values[0])
        rewards_config = self.bot.config.get('loyalty_program', {}).get('rewards', [])
        reward_info = next((r for r in rewards_config if r['points'] == selected_points), None)
        
        users = await self.bot.load_json('users') # Using bot's load_json
        user_id_str = str(interaction.user.id)
        user_data = users.get(user_id_str, {})
        user_points = user_data.get('points', 0)

        if not reward_info:
            await interaction.followup.send("❌ This reward is no longer available or invalid.", ephemeral=True)
            return

        if user_points < reward_info['points']:
            await interaction.followup.send("❌ You no longer have enough points for this reward.", ephemeral=True)
            return

        # Deduct points
        user_data['points'] -= reward_info['points']
        users[user_id_str] = user_data # Update the user data in the main 'users' dict
        await self.bot.save_json('users', users) # Using bot's save_json

        # Generate and save discount code
        discounts = await self.bot.load_json('discounts') # Using bot's load_json
        code = f"REDEEM-{''.join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(6))}"
        discounts[code] = {
            "type": "redeem", 
            "discount_inr": reward_info['discount_inr'],
            "used": False,
            "generated_by": user_id_str # Track who generated it
        }
        await self.bot.save_json('discounts', discounts) # Using bot's save_json

        # DM the user
        try:
            embed = discord.Embed(
                title="✨ Reward Redeemed!",
                description=f"You have successfully redeemed **{reward_info['points']} points** for a **₹{reward_info['discount_inr']:.2f}** discount.",
                color=int(self.bot.config['success_color'], 16)
            )
            embed.add_field(name="Your One-Time Discount Code", value=f"`{code}`")
            embed.set_footer(text="Use the 'Apply Discount' button in your shopping cart to use this code.")
            await interaction.user.send(embed=embed)
            await interaction.followup.send("✅ Your discount code has been sent to your DMs!", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("⚠️ I couldn't DM you your code. Please enable DMs from server members and contact staff if you need assistance.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send("❌ An error occurred while sending your code via DM. Please contact staff.", ephemeral=True)
            print(f"Error sending redeem code DM: {e}")

class RedeemView(discord.ui.View):
    def __init__(self, bot, affordable_rewards: list):
        super().__init__(timeout=180) # Timeout after 3 minutes
        self.add_item(RedeemSelect(bot, affordable_rewards))
        # Add a custom_id for persistent views if this were to be deployed persistently
        # self.custom_id = "redeem_view_persistent"

class LoyaltyProgram(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _update_user_roles(self, interaction: discord.Interaction, user: discord.Member, new_points_balance: int):
        """
        Checks a user's points and assigns/removes roles accordingly.
        This revised logic ensures a user has only the single highest role they qualify for.
        """
        if not interaction.guild or not isinstance(user, discord.Member):
            print(f"Skipping role update: Interaction not in a guild or user is not a member (User ID: {user.id})")
            return
        
        rewards_config = self.bot.config.get('loyalty_program', {}).get('role_rewards', [])
        if not rewards_config:
            return

        # Sort rewards by points required, highest first
        sorted_rewards = sorted(rewards_config, key=lambda r: r['points'], reverse=True)
        
        # Determine the single best role the user qualifies for
        target_role_id = None
        for reward in sorted_rewards:
            if new_points_balance >= reward['points']:
                target_role_id = reward['role_id']
                break # Found the highest role they qualify for

        # Get a set of all possible reward role IDs for quick lookup
        all_reward_role_ids = {reward['role_id'] for reward in rewards_config}
        
        roles_to_remove = []
        user_has_target_role = False
        
        current_user_role_ids = {role.id for role in user.roles} # Get actual role IDs the user has
        
        for role_id in all_reward_role_ids:
            if role_id in current_user_role_ids:
                if role_id == target_role_id:
                    user_has_target_role = True
                else:
                    # This is a reward role, but not the one they should have
                    if (role_obj := interaction.guild.get_role(role_id)): # Get role object
                        roles_to_remove.append(role_obj)
        
        # Remove any incorrect loyalty roles
        if roles_to_remove:
            try:
                await user.remove_roles(*roles_to_remove, reason="Updating loyalty tier.")
            except discord.Forbidden:
                print(f"Bot lacks permissions to remove roles for user {user.id}.")
            except Exception as e:
                print(f"Error removing roles for user {user.id}: {e}")
            
        # Add the correct role if they don't have it and they qualify for one
        if target_role_id and not user_has_target_role:
            target_role = interaction.guild.get_role(target_role_id)
            if target_role:
                try:
                    await user.add_roles(target_role, reason=f"Reached {new_points_balance} loyalty points.")
                except discord.Forbidden:
                    print(f"Bot lacks permissions to add role {target_role_id} to user {user.id}.")
                except Exception as e:
                    print(f"Error adding role {target_role_id} to user {user.id}: {e}")
            else:
                print(f"Target role {target_role_id} not found in guild for user {user.id}.")


    @app_commands.command(name="mypoints", description="Check your Infinity Points balance.")
    async def mypoints(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        users = await self.bot.load_json('users') # Using bot's load_json
        points = users.get(str(interaction.user.id), {'points': 0})['points']
        
        embed = discord.Embed(
            title="✨ Your Infinity Points",
            description=f"You currently have **{points}** points.",
            color=int(self.bot.config['embed_color'], 16)
        )
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="redeem", description="Spend your Infinity Points on discount codes.")
    async def redeem(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        users = await self.bot.load_json('users') # Using bot's load_json
        user_points = users.get(str(interaction.user.id), {'points': 0})['points']
        
        # Access rewards configuration correctly
        all_rewards = self.bot.config.get('loyalty_program', {}).get('rewards', [])
        # Ensure rewards are sorted by points for consistent display
        sorted_all_rewards = sorted(all_rewards, key=lambda r: r.get('points', 0))
        
        affordable_rewards = [r for r in sorted_all_rewards if user_points >= r.get('points', 0)]

        embed = discord.Embed(
            title="🎁 Point Redemption Store",
            description=f"You have **{user_points}** points to spend.\n\n"
                        "Select a reward from the dropdown below to redeem your points.",
            color=int(self.bot.config['embed_color'], 16)
        )
        view = RedeemView(self.bot, affordable_rewards)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    @app_commands.command(name="leaderboard", description="View the top 10 users with the most Infinity Points.")
    async def leaderboard(self, interaction: discord.Interaction):
        await interaction.response.defer()
        users_data = await self.bot.load_json('users') # Using bot's load_json

        if not users_data:
            await interaction.followup.send("There's no one on the leaderboard yet!")
            return

        # Sort users by points, descending, and filter out those with 0 points.
        sorted_users = sorted(
            [item for item in users_data.items() if item[1].get('points', 0) > 0],
            key=lambda item: item[1].get('points', 0), # Ensure default for sorting if 'points' missing
            reverse=True
        )

        embed = discord.Embed(
            title="🏆 Infinity Points Leaderboard",
            color=int(self.bot.config['embed_color'], 16)
        )

        leaderboard_lines = []
        for i, (user_id, data) in enumerate(sorted_users[:10]): # Show top 10
            try:
                user = await self.bot.fetch_user(int(user_id))
                rank_emoji = {0: "🥇", 1: "🥈", 2: "🥉"}.get(i, f"**{i+1}.**")
                leaderboard_lines.append(f"{rank_emoji} {user.mention} - `{data['points']}` points")
            except discord.NotFound:
                print(f"User {user_id} not found for leaderboard. Skipping.")
                continue # Skip users the bot can't find
            except Exception as e:
                print(f"Error processing user {user_id} for leaderboard: {e}")
                continue

        if not leaderboard_lines:
            embed.description = "No users with points have been recorded yet."
        else:
            embed.description = "\n".join(leaderboard_lines)

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="adjust_points", description="[OWNER] Manually add or remove points from a user.")
    @is_owner()
    async def adjust_points(self, interaction: discord.Interaction, user: discord.User, amount: int, reason: str = "Manual adjustment by owner."):
        await interaction.response.defer(ephemeral=True)
        
        users = await self.bot.load_json('users') # Using bot's load_json
        user_id_str = str(user.id)
        
        if user_id_str not in users:
            users[user_id_str] = {'points': 0}
            
        users[user_id_str]['points'] = max(0, users[user_id_str]['points'] + amount) # Ensure points don't go below zero
        new_balance = users[user_id_str]['points']
        
        await self.bot.save_json('users', users) # Using bot's save_json

        # Attempt to get discord.Member object for role update
        member = None
        if interaction.guild:
            member = interaction.guild.get_member(user.id)
            if not member: # Try fetching if not in cache
                try:
                    member = await interaction.guild.fetch_member(user.id)
                except discord.NotFound:
                    print(f"User {user.id} not found in guild {interaction.guild.id} for role adjustment.")
                except Exception as e:
                    print(f"Error fetching member {user.id} for role adjustment: {e}")

        if member:
            await self._update_user_roles(interaction, member, new_balance)
        
        # Notify the target user via DM
        try:
            embed = discord.Embed(
                title="✨ Your Points Have Been Updated",
                description="An admin has adjusted your Infinity Points balance.",
                color=int(self.bot.config['embed_color'], 16)
            )
            embed.add_field(name="Amount", value=f"`{amount:+}` points") # Shows + or -
            embed.add_field(name="New Balance", value=f"`{new_balance}` points")
            embed.add_field(name="Reason", value=reason, inline=False)
            await user.send(embed=embed)
        except discord.Forbidden:
            await interaction.followup.send(f"✅ Successfully adjusted {user.mention}'s points by `{amount:+}`. Their new balance is `{new_balance}`. (Could not DM user: DMs closed).", ephemeral=True)
            pass # User has DMs closed
        except Exception as e:
            await interaction.followup.send(f"✅ Successfully adjusted {user.mention}'s points by `{amount:+}`. Their new balance is `{new_balance}`. (Error sending DM: {e}).", ephemeral=True)
            print(f"Error sending adjust_points DM to {user.id}: {e}")
            
        await interaction.followup.send(f"✅ Successfully adjusted {user.mention}'s points by `{amount:+}`. Their new balance is `{new_balance}`.", ephemeral=True)
        
async def setup(bot: commands.Bot):
    await bot.add_cog(LoyaltyProgram(bot))
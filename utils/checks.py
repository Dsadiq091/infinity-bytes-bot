# utils/checks.py
from discord import app_commands, Interaction

def is_owner():
    """A check for commands that should only be usable by a bot owner."""
    async def predicate(interaction: Interaction) -> bool:
        return interaction.user.id in interaction.client.config.get('owner_ids', [])
    return app_commands.check(predicate)

def is_staff_or_owner():
    """A check for commands usable by staff or a bot owner."""
    async def predicate(interaction: Interaction) -> bool:
        owner_ids = interaction.client.config.get('owner_ids', [])
        staff_ids = interaction.client.config.get('staff_role_ids', [])
        if interaction.user.id in owner_ids:
            return True
        # Check if the user has any of the staff roles
        return any(role.id in staff_ids for role in interaction.user.roles)
    return app_commands.check(predicate)
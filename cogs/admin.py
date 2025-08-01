# cogs/admin.py
import discord
from discord.ext import commands
from discord import app_commands
import datetime
from datetime import timedelta
import csv
import io
from utils.checks import is_owner

class AdminTools(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="create_discount", description="[OWNER] Creates a server-wide promotional discount code.")
    @is_owner()
    @app_commands.describe(
        code="The code users will type (e.g., SUMMER25)",
        discount_inr="The discount amount in INR (e.g., 25.0)",
        max_uses="The total number of times this code can be used (0 for infinite).", # Clarified 0 for infinite
        expires_in_days="Optional: The number of days until this code expires (e.g., 7)."
    )
    async def create_discount(self, interaction: discord.Interaction, code: str, discount_inr: float, max_uses: int, expires_in_days: int = None):
        await interaction.response.defer(ephemeral=True)
        discounts = await self.bot.load_json('discounts') # Using bot's load_json
        code = code.strip().upper()

        if discount_inr <= 0:
            await interaction.followup.send("❌ Discount amount must be a positive number.", ephemeral=True)
            return
        if max_uses < 0:
            await interaction.followup.send("❌ Maximum uses cannot be negative. Use 0 for infinite uses.", ephemeral=True)
            return

        if code in discounts:
            await interaction.followup.send(f"❌ A discount code named `{code}` already exists. Please choose a different code.", ephemeral=True)
            return
        
        expiry_timestamp = None
        if expires_in_days is not None:
            if expires_in_days <= 0:
                await interaction.followup.send("❌ Expiry in days must be a positive number.", ephemeral=True)
                return
            expiry_date = datetime.datetime.now(datetime.timezone.utc) + timedelta(days=expires_in_days)
            expiry_timestamp = expiry_date.isoformat()

        discounts[code] = {
            "type": "promo", 
            "discount_inr": discount_inr, 
            "max_uses": max_uses if max_uses > 0 else float('inf'), # Store as inf for infinite
            "uses": 0, 
            "expires_at": expiry_timestamp, 
            "is_active": True
        }
        await self.bot.save_json('discounts', discounts) # Using bot's save_json

        expiry_msg = f" It expires in **{expires_in_days} days**." if expires_in_days else ""
        max_uses_msg = f" **{max_uses}** times" if max_uses > 0 else "**infinite** times"

        await interaction.followup.send(f"✅ Successfully created promotional code `{code}` for a **₹{discount_inr:.2f}** discount.\nIt can be used {max_uses_msg}.{expiry_msg}", ephemeral=True)

    @app_commands.command(name="export_products", description="[OWNER] Export all products to a CSV file.")
    @is_owner()
    async def export_products(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        products = await self.bot.load_json('products') # Using bot's load_json
        if not products:
            await interaction.followup.send("There are no products to export.", ephemeral=True)
            return
        
        output = io.StringIO()
        # Ensure all relevant fields are included, matching ProductModal structure
        fieldnames = ['product_id', 'name', 'description', 'price', 'stock', 'emoji', 'image_url', 'renewal_period_days']
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        
        for pid, data in products.items():
            row = data.copy()
            row['product_id'] = pid
            # Ensure all fields are present for consistency, even if None
            for field in fieldnames:
                if field not in row:
                    row[field] = None # Add missing fields with None
            writer.writerow(row)
            
        output.seek(0)
        await interaction.followup.send("Here is your product data:", file=discord.File(output, "products_export.csv"), ephemeral=True)

    @app_commands.command(name="import_products", description="[OWNER] Import/Update products from a CSV file.")
    @is_owner()
    async def import_products(self, interaction: discord.Interaction, file: discord.Attachment):
        await interaction.response.defer(ephemeral=True)
        if not file.filename.endswith('.csv'):
            await interaction.followup.send("❌ Please upload a valid CSV file (e.g., `products.csv`).", ephemeral=True)
            return

        products = await self.bot.load_json('products') # Using bot's load_json
        updated_count = 0
        added_count = 0
        skipped_count = 0
        errors = []

        try:
            content = await file.read()
            csv_file = io.StringIO(content.decode('utf-8'))
            reader = csv.DictReader(csv_file)
            
            # Define expected fields and their types/defaults for parsing
            expected_fields = {
                'name': str, 'description': str, 'price': float, 
                'stock': int, 'emoji': str, 'image_url': str, 
                'renewal_period_days': int
            }

            for i, row in enumerate(reader):
                line_num = i + 2 # +1 for header, +1 for 0-index
                pid = row.get('product_id', '').strip()
                if not pid:
                    errors.append(f"Line {line_num}: `product_id` is missing or empty. Skipping row.")
                    skipped_count += 1
                    continue

                new_product_data = {}
                is_valid_row = True

                for field, field_type in expected_fields.items():
                    value_str = row.get(field, '').strip()
                    if value_str:
                        try:
                            if field_type == float:
                                parsed_value = float(value_str)
                                if field == 'price' and parsed_value < 0:
                                    raise ValueError("Price cannot be negative.")
                            elif field_type == int:
                                parsed_value = int(value_str)
                                if field == 'stock' and parsed_value < -1:
                                    raise ValueError("Stock cannot be less than -1.")
                                if field == 'renewal_period_days' and parsed_value <= 0:
                                    raise ValueError("Renewal period must be positive.")
                            else: # For string fields like emoji, image_url, name, description
                                parsed_value = value_str
                            new_product_data[field] = parsed_value
                        except ValueError as ve:
                            errors.append(f"Line {line_num}, Product `{pid}`: Invalid value for '{field}' ('{value_str}'). {ve}. Skipping row.")
                            is_valid_row = False
                            break
                    else: # Handle empty optional fields as None, required fields might error later if not provided
                        if field in ['name', 'description', 'price', 'stock']: # Assuming these are required
                            if not (field == 'price' or field == 'stock'): # Price and stock can be 0 or -1, but must be there
                                errors.append(f"Line {line_num}, Product `{pid}`: Required field '{field}' is empty. Skipping row.")
                                is_valid_row = False
                                break
                        new_product_data[field] = None # Set empty optional fields to None

                if not is_valid_row:
                    skipped_count += 1
                    continue
                
                # Assign defaults if values are still missing after parsing
                if 'price' not in new_product_data or new_product_data['price'] is None: new_product_data['price'] = 0.0 # Default price
                if 'stock' not in new_product_data or new_product_data['stock'] is None: new_product_data['stock'] = -1 # Default infinite stock

                if pid in products: 
                    updated_count += 1
                else: 
                    added_count += 1
                
                products[pid] = new_product_data

            await self.bot.save_json('products', products) # Using bot's save_json

            response_message = f"✅ Import successful!\n- **Updated:** {updated_count} products\n- **Added:** {added_count} new products"
            if skipped_count > 0:
                response_message += f"\n- **Skipped:** {skipped_count} rows due to errors."
            
            if errors:
                error_details = "\n".join(errors[:5]) # Show first 5 errors
                if len(errors) > 5:
                    error_details += f"\n...and {len(errors) - 5} more errors."
                response_message += f"\n\n**Import Errors:**\n```\n{error_details}\n```"

            await interaction.followup.send(response_message, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ An unexpected error occurred during import: {e}", ephemeral=True)
            print(f"Error during import_products: {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(AdminTools(bot))
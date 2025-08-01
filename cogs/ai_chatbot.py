# cogs/ai_chatbot.py
import discord
from discord.ext import commands
from discord import app_commands
from groq import Groq
import os
from utils.checks import is_owner

class AIChatbot(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Check if GROQ_API_KEY is available
        groq_api_key = os.getenv("GROQ_API_KEY")
        if not groq_api_key:
            print("WARNING: GROQ_API_KEY environment variable not set. AI Chatbot functionality will be limited or disabled.")
            self.client = None # Set client to None if API key is missing
        else:
            self.client = Groq(api_key=groq_api_key)
            
        self.knowledge_base = self.load_knowledge_base()

    def load_knowledge_base(self):
        """Loads the knowledge base file into memory."""
        # The path in config.json is likely relative to the bot's root directory.
        filepath_from_config = self.bot.config.get("knowledge_base_file")
        if not filepath_from_config:
            print("Warning: 'knowledge_base_file' not specified in config.json.")
            return "No knowledge base provided."
            
        # Construct the absolute path from the bot's root directory
        filepath = os.path.join(os.getcwd(), filepath_from_config)

        if os.path.exists(filepath):
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                    print(f"Knowledge base loaded successfully from: {filepath}")
                    return content
            except Exception as e:
                print(f"Error loading knowledge base from {filepath}: {e}")
        else:
            print(f"Warning: Knowledge base file not found at: {filepath}")
        return "No knowledge base provided."

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        """Listen for mentions and respond with context."""
        if message.author.bot or self.bot.user not in message.mentions:
            return
        
        if not self.client: # Check if Groq client was initialized
            await message.reply("Sorry, the AI assistant is currently unavailable (API key missing).")
            return

        # Acknowledge the mention
        async with message.channel.typing():
            question = message.content.replace(f'<@{self.bot.user.id}>', '').strip()

            # --- Fetch User Context ---
            orders = await self.bot.load_json('orders') # Using bot's load_json
            users = await self.bot.load_json('users') # Using bot's load_json
            user_points = users.get(str(message.author.id), {}).get('points', 0)
            
            user_orders_list = []
            # Filter orders for the current user and get relevant info
            for oid, o in orders.items():
                if str(o.get('user_id')) == str(message.author.id):
                    # Shorten order ID for display and include status
                    user_orders_list.append(f"- Order #{oid[:6]} (Status: {o.get('status', 'Unknown')})")
            
            user_context = f"""
            --- USER CONTEXT ---
            User Name: {message.author.display_name}
            Infinity Points: {user_points}
            Recent Orders: {', '.join(user_orders_list[-3:]) if user_orders_list else 'None'}
            --- END USER CONTEXT ---
            """

            system_prompt = f"""You are a helpful and friendly store assistant named {self.bot.user.name}.
            Answer the user's question based on the knowledge base and their personal context provided below.
            If the answer isn't in the knowledge base, politely say you don't know and suggest opening a ticket for staff assistance.
            Do not make up information.

            --- KNOWLEDGE BASE ---
            {self.knowledge_base}
            --- END KNOWLEDGE BASE ---
            {user_context}
            """
            
            try:
                chat_completion = self.client.chat.completions.create(
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": question}
                    ],
                    model="llama3-8b-8192", # Ensure this model is available on your Groq plan
                    temperature=0.7, # Adjust creativity
                    max_tokens=500 # Limit response length
                )
                answer = chat_completion.choices[0].message.content
                await message.reply(answer)
            except Exception as e:
                print(f"Error during AI chat completion for on_message: {e}")
                await message.reply(f"Sorry, I couldn't process that question right now. My AI brain might be taking a coffee break! Please try again later, or open a ticket if it's urgent.")

    @app_commands.command(name="ask", description="Ask the AI assistant a question about our store.")
    async def ask(self, interaction: discord.Interaction, question: str):
        """Answers questions using the loaded knowledge base."""
        await interaction.response.defer()
        
        if not self.client:
            await interaction.followup.send("Sorry, the AI assistant is currently unavailable (API key missing).", ephemeral=True)
            return

        system_prompt = f"""You are a helpful and friendly store assistant named {self.bot.user.name}.
        Your goal is to answer user questions based *only* on the information provided in the knowledge base below.
        If the answer is not in the knowledge base, politely say you don't have that information and suggest opening a ticket for staff assistance.
        Do not make up information.

        --- KNOWLEDGE BASE ---
        {self.knowledge_base}
        --- END KNOWLEDGE BASE ---
        """
        
        try:
            chat_completion = self.client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": question}
                ],
                model="llama3-8b-8192",
                temperature=0.7,
                max_tokens=500
            )
            answer = chat_completion.choices[0].message.content
            await interaction.followup.send(f"**Question:** {question}\n\n**Answer:** {answer}")
        except Exception as e:
            print(f"Error during AI chat completion for /ask command: {e}")
            await interaction.followup.send(f"Sorry, the AI service is currently unavailable. Please try again later, or open a ticket for direct assistance.", ephemeral=True)

    async def generate_support_suggestion(self, product_info: str, user_problem: str):
        """Generates a first-response for a support ticket using the knowledge base."""
        if not self.client:
            return "Thank you for the details. Please wait while a staff member reviews your case (AI is offline)."

        system_prompt = f"""You are an automated support assistant for a Discord store.
        A user has opened a ticket and needs help with a product.
        Your goal is to provide a helpful first-response based on their problem and the knowledge base provided.
        Look for specific troubleshooting steps or common solutions related to the product or problem.
        If you find a relevant troubleshooting step in the knowledge base, suggest it.
        If no specific solution is found in the knowledge base, provide a generic but helpful message telling them staff will investigate further.
        Keep the response concise and actionable.

        --- KNOWLEDGE BASE ---
        {self.knowledge_base}
        --- END KNOWLEDGE BASE ---
        """
        user_prompt = f"The user has an issue with the product '{product_info}'. Their problem is: '{user_problem}'"
        try:
            chat_completion = self.client.chat.completions.create(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
                model="llama3-8b-8192",
                temperature=0.5, # Lower temperature for less creative, more factual answers
                max_tokens=300
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            print(f"AI Support Suggestion Error: {e}")
            return "Thank you for the details. Please wait while a staff member reviews your case, as the AI assistant encountered an issue generating a suggestion."

    async def generate_summary(self, history: list[discord.Message]):
        """Generates a summary of a ticket conversation for archival."""
        if not self.client:
            return "Summary could not be generated (AI is offline)."
            
        # Prepare messages in chronological order (already reversed in close_ticket_action)
        formatted_history = "\n".join([f"{msg.author.display_name} ({msg.author.id}): {msg.content}" for msg in history])
        
        system_prompt = "You are a helpful assistant. Summarize the following Discord ticket conversation into a single, concise paragraph for archival purposes. Focus on the initial problem, the steps taken, and the final resolution. If a resolution is not clear, state that the issue is ongoing or unresolved. Keep it under 200 words."
        
        try:
            chat_completion = self.client.chat.completions.create(
                messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": formatted_history}],
                model="llama3-8b-8192",
                temperature=0.3, # Low temperature for factual summary
                max_tokens=200 # Max tokens to control summary length
            )
            return chat_completion.choices[0].message.content
        except Exception as e:
            print(f"AI Summary Error: {e}")
            return "Summary could not be generated due to an AI service error."

    @app_commands.command(name="reload_knowledge", description="[OWNER] Reloads the AI's knowledge base from the file.")
    @is_owner()
    async def reload_knowledge(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        self.knowledge_base = self.load_knowledge_base()
        await interaction.followup.send("✅ AI knowledge base has been reloaded.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(AIChatbot(bot))
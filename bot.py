import os
import discord
from discord.ext import commands, tasks
import pytesseract
from PIL import Image
import io
import re
import datetime

# ========================================
#  ΡΥΘΜΙΣΕΙΣ BOT
# ========================================
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")   # Load token from Render
PROOF_CHANNEL_ID = 1433200267947671604       # Kanali apodeixewn
PAYMENTS_CHANNEL_ID = 1433226571501535282    # Kanali plirwmwn

# Tesseract path (Render uses Linux, so leave None)
TESSERACT_PATH = None
if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

# OCR setup
def extract_numbers_from_image(image_bytes):
    """Reads image and extracts all numbers from OCR."""
    try:
        img = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(img, lang="eng")
        numbers = re.findall(r'\d+', text)
        return sum(map(int, numbers)) if numbers else 0
    except Exception:
        return 0


# ========================================
# ΡΟΛΟΙ & ΠΟΣΟΣΤΑ
# ========================================
ROLE_PERCENTAGES = {
    "Original Boss": 0.30,
    "Vice Boss": 0.25,
    "Manager": 0.20,
    "Worker": 0.15,
    "Delivery": 0.10
}

# Weekly storage
weekly_totals = {}


# ========================================
# BOT SETUP
# ========================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ========================================
# ON READY
# ========================================
@bot.event
async def on_ready():
    print(f"✅ Bot συνδέθηκε ως {bot.user}")
    weekly_payroll.start()   # Start weekly payroll loop


# ========================================
# LISTENER: READS IMAGES FROM PROOF CHANNEL
# ========================================
@bot.event
async def on_message(message):
    if message.channel.id == PROOF_CHANNEL_ID and message.attachments:
        user_id = str(message.author.id)

        for attachment in message.attachments:
            bytes_data = await attachment.read()
            value = extract_numbers_from_image(bytes_data)

            if user_id not in weekly_totals:
                weekly_totals[user_id] = 0

            weekly_totals[user_id] += value
            print(f"Added {value} for {message.author}. Total now {weekly_totals[user_id]}")

    await bot.process_commands(message)


# ========================================
# WEEKLY PAYROLL (EVERY FRIDAY)
# ========================================
@tasks.loop(minutes=1)
async def weekly_payroll():
    now = datetime.datetime.utcnow()

    # Run every Friday at 12:00 UTC (14:00 Greece/Cyprus)
    if now.weekday() == 4 and now.hour == 12 and now.minute == 0:
        channel = bot.get_channel(PAYMENTS_CHANNEL_ID)
        if not channel:
            print("Error: Payments channel not found")
            return

        await channel.send("📅 **ΥΠΟΛΟΓΙΣΜΟΣ ΜΙΣΘΩΝ ΠΑΡΑΣΚΕΥΗΣ...**")

        for user_id, total in weekly_totals.items():
            user = bot.get_user(int(user_id))
            if not user:
                continue

            # Find user role
            member = None
            for guild in bot.guilds:
                member = guild.get_member(int(user_id))
                if member:
                    break

            if not member:
                continue

            # Determine percentage
            percentage = 0
            for role in member.roles:
                if role.name in ROLE_PERCENTAGES:
                    percentage = ROLE_PERCENTAGES[role.name]

            final_pay = int(total * percentage)

            await channel.send(
                f"💼 **{member.display_name}**\n"
                f"📊 Σύνολο εβδομάδας: **{total}**\n"
                f"🏅 Ρόλος: **{percentage * 100}%**\n"
                f"💸 **Τελικός μισθός: {final_pay}**"
            )

        # Reset totals after payroll
        weekly_totals.clear()
        print("Weekly totals reset.")


# ========================================
# RUN BOT
# ========================================
if __name__ == "__main__":
    if DISCORD_TOKEN is None:
        print("❌ ERROR: Το DISCORD_TOKEN δεν βρέθηκε στο Render!")
    else:
        bot.run(DISCORD_TOKEN)





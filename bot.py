# -*- coding: utf-8 -*-
"""
Discord OCR Payroll Bot (Greek)
-------------------------------
• Διαβάζει screenshots από κανάλι "proof" (ID: 1433200267947671604)
• Κάνει OCR, βρίσκει αριθμούς, τους αθροίζει
• Αποθηκεύει ανά χρήστη & μέρα (SQLite DB)
• Κάθε Παρασκευή κάνει payout με βάση ρόλο & ποσοστό
• Στέλνει τελικό αποτέλεσμα στο κανάλι "payments" (ID: 1433226571501535282)
• Ρόλοι:
    Original Boss → 30%
    Vice Boss     → 25%
    Manager       → 20%
    Worker        → 15%
    Delivery      → 10%
"""

import os
import re
import sqlite3
from io import BytesIO
from datetime import date, datetime, timedelta

import discord
from discord.ext import commands, tasks
from PIL import Image, ImageOps, ImageFilter
import pytesseract

# ================== ΡΥΘΜΙΣΕΙΣ ΧΡΗΣΤΗ ==================

# ΒΑΛΕ ΕΔΩ ΤΟ TOKEN ΤΟΥ BOT ΣΟΥ
DISCORD_TOKEN = "MTQ0MTk1Njg5ODYyNTgxODg1NA.GZMvhK.PGrUi_SfspAlRp7wc3HAKc0Ur3L_99bERs0j7A"

# IDs καναλιών (τα έχεις ήδη δώσει)
PROOF_CHANNEL_ID = 1433200267947671604      # κανάλι αποδείξεων
PAYMENTS_CHANNEL_ID = 1433226571501535282   # κανάλι πληρωμών

# Αν χρειάζεται, βάλε path για το Tesseract (Windows)
# π.χ. r"C:\Program Files\Tesseract-OCR\tesseract.exe"
TESSERACT_PATH = None

if TESSERACT_PATH:
    pytesseract.pytesseract.tesseract_cmd = TESSERACT_PATH

# SQLite DB (θα δημιουργηθεί μόνο του)
DB_PATH = "payroll_data.db"

# Νόμισμα (απλά για εμφάνιση)
CURRENCY_SYMBOL = "€"

# Ρόλοι & ποσοστά
ROLE_PERCENTAGES = {
    "Original Boss": 0.30,
    "Vice Boss": 0.25,
    "Manager": 0.20,
    "Worker": 0.15,
    "Delivery": 0.10,
}

# Σειρά προτεραιότητας ρόλων (πιο πάνω = πιο «δυνατός»)
ROLE_PRIORITY = [
    "Original Boss",
    "Vice Boss",
    "Manager",
    "Worker",
    "Delivery",
]

# ================== DISCORD INTENTS & BOT ==================

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix="!", intents=intents)


# ================== DATABASE HELPERS ==================

def init_db():
    """Δημιουργία πινάκων αν δεν υπάρχουν."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Πίνακας contributions: τιμές ανά user & ημερομηνία
    c.execute("""
        CREATE TABLE IF NOT EXISTS contributions (
            user_id TEXT NOT NULL,
            date TEXT NOT NULL,
            value REAL NOT NULL
        )
    """)
    # Πίνακας settings: για last_payout_date κτλ
    c.execute("""
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()
    conn.close()


def db_insert_contribution(user_id: int, value: float):
    """Αποθηκεύει contribution για σήμερα για συγκεκριμένο user."""
    today_iso = date.today().isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT INTO contributions (user_id, date, value) VALUES (?, ?, ?)",
        (str(user_id), today_iso, float(value))
    )
    conn.commit()
    conn.close()


def db_get_setting(key: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key = ?", (key,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def db_set_setting(key: str, value: str):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
        (key, value)
    )
    conn.commit()
    conn.close()


def get_current_week_range():
    """
    Επιστρέφει (monday_iso, friday_iso) για την τρέχουσα εβδομάδα.
    Δευτέρα=0 ... Κυριακή=6
    """
    today = date.today()
    weekday = today.weekday()  # 0=Mon, 4=Fri
    monday = today - timedelta(days=weekday)
    friday = monday + timedelta(days=4)
    return monday.isoformat(), friday.isoformat()


def db_get_weekly_totals():
    """Επιστρέφει dict {user_id: total_value} για τρέχουσα εβδομάδα (Δευ–Παρ)."""
    monday_iso, friday_iso = get_current_week_range()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT user_id, SUM(value) AS total
        FROM contributions
        WHERE date BETWEEN ? AND ?
        GROUP BY user_id
    """, (monday_iso, friday_iso))
    rows = c.fetchall()
    conn.close()
    return {user_id: total for user_id, total in rows}


def db_clear_current_week():
    """Σβήνει τα δεδομένα της τρέχουσας εβδομάδας (Δευ–Παρ)."""
    monday_iso, friday_iso = get_current_week_range()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "DELETE FROM contributions WHERE date BETWEEN ? AND ?",
        (monday_iso, friday_iso)
    )
    conn.commit()
    conn.close()


# ================== OCR HELPERS ==================

def preprocess_image(pil_img: Image.Image) -> Image.Image:
    """Ελαφρύ preprocessing για καλύτερο OCR."""
    img = pil_img.convert("L")  # grayscale
    img = ImageOps.autocontrast(img)
    img = img.filter(ImageFilter.SHARPEN)
    w, h = img.size
    if w < 800:
        img = img.resize((int(w * 1.5), int(h * 1.5)))
    return img


def extract_numbers_from_text(text: str):
    """
    Παίρνουμε όλους τους αριθμούς από το text, με υποστήριξη και για 1.234,56 μορφές.
    Επιστρέφει λίστα από floats.
    """
    raw = re.findall(r"[-+]?[0-9]+(?:[.,][0-9]{1,})?", text)
    numbers = []

    for token in raw:
        t = token.replace(" ", "")

        if t.count(".") > 1 or t.count(",") > 1:
            t = t.replace(".", "").replace(",", "")

        if "." in t and "," in t:
            # Κρατάμε το τελευταίο ως δεκαδικό
            if t.rfind(".") > t.rfind(","):
                t = t.replace(",", "")
            else:
                t = t.replace(".", "").replace(",", ".")

        t = t.replace(",", ".")

        try:
            num = float(t)
            numbers.append(num)
        except ValueError:
            continue

    return numbers


# ================== ROLE LOGIC ==================

def get_role_multiplier(member: discord.Member):
    """
    Βρίσκει το ποσοστό με βάση τον υψηλότερο ρόλο που έχει ο χρήστης
    από τη λίστα ROLE_PRIORITY.
    """
    if member is None:
        return 0.0, None

    for role_name in ROLE_PRIORITY:
        for r in member.roles:
            if r.name == role_name:
                return ROLE_PERCENTAGES.get(role_name, 0.0), role_name

    return 0.0, None  # δεν έχει κανέναν από τους ρόλους μας


# ================== DISCORD EVENTS ==================

@bot.event
async def on_ready():
    print(f"✅ Συνδέθηκε ως: {bot.user} (ID: {bot.user.id})")
    init_db()
    # Ξεκινάει το daily check (για αυτόματο payout Παρασκευής)
    daily_check.start()


@bot.event
async def on_message(message: discord.Message):
    # Πάντα πρώτα για να δουλεύουν οι commands
    await bot.process_commands(message)

    # Αγνοούμε μηνύματα από bots
    if message.author.bot:
        return

    # Θέλουμε ΜΟΝΟ το κανάλι PROOF
    if message.channel.id != PROOF_CHANNEL_ID:
        return

    if not message.attachments:
        return

    for attachment in message.attachments:
        if not any(attachment.filename.lower().endswith(ext) for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp")):
            continue

        try:
            data = await attachment.read()
            img = Image.open(BytesIO(data))
        except Exception as e:
            await message.channel.send(
                f"{message.author.mention} ❌ Σφάλμα στο άνοιγμα της εικόνας: `{e}`"
            )
            continue

        try:
            pre = preprocess_image(img)
            # Αν θέλεις και ελληνικά, βάλε lang='eng+ell' και να έχεις ελληνικό tessdata
            text = pytesseract.image_to_string(pre, lang="eng")
        except Exception as e:
            await message.channel.send(
                f"{message.author.mention} ❌ Σφάλμα OCR: `{e}`"
            )
            continue

        numbers = extract_numbers_from_text(text)

        if not numbers:
            await message.channel.send(
                f"{message.author.mention} ❕ Δεν βρήκα αριθμούς στην απόδειξη."
            )
            continue

        total = sum(numbers)
        db_insert_contribution(message.author.id, total)

        await message.channel.send(
            f"🧾 {message.author.mention} βρήκα τους αριθμούς: `{', '.join(str(n) for n in numbers)}`\n"
            f"➕ Άθροισμα απόδειξης: **{total:.2f}{CURRENCY_SYMBOL}** (προστέθηκε στο εβδομαδιαίο σου σύνολο)."
        )


# ================== AUTOMATIC WEEKLY PAYOUT ==================

@tasks.loop(hours=1)
async def daily_check():
    """
    Τσεκάρει κάθε 1 ώρα:
    • Αν είναι Παρασκευή
    • Αν δεν έχει ήδη γίνει payout σήμερα
    • Αν ναι, κάνει αυτόματα payout
    """
    today = date.today()
    weekday = today.weekday()  # 0=Δευ, 4=Παρ

    if weekday != 4:
        return

    last_payout = db_get_setting("last_payout_date")
    if last_payout == today.isoformat():
        # Ήδη έγινε payout σήμερα
        return

    # Κάνουμε αυτόματο payout
    await run_payout(automatic=True)
    db_set_setting("last_payout_date", today.isoformat())


async def run_payout(automatic: bool = False, ctx: commands.Context = None):
    """
    Κοινή λογική για payout (είτε αυτόματο, είτε με command).
    """
    channel = bot.get_channel(PAYMENTS_CHANNEL_ID)
    if channel is None:
        if ctx:
            await ctx.send("❌ Δεν βρήκα το κανάλι payments ή δεν έχω πρόσβαση.")
        else:
            print("❌ Δεν βρήκα το κανάλι payments ή δεν έχω πρόσβαση.")
        return

    totals = db_get_weekly_totals()

    if not totals:
        msg = "📢 **Εβδομαδιαία Πληρωμή**\n\nΔεν υπάρχουν καταχωρημένες αποδείξεις για αυτή την εβδομάδα."
        await channel.send(msg)
        return

    title = "📢 **Εβδομαδιαία Πληρωμή (Αυτόματο)**" if automatic else "📢 **Εβδομαδιαία Πληρωμή (Χειροκίνητο)**"

    lines = [title, ""]
    guild = channel.guild

    # Ταξινόμηση κατά σύνολο (φθίνουσα)
    for user_id, total in sorted(totals.items(), key=lambda kv: kv[1], reverse=True):
        member = guild.get_member(int(user_id))
        if member is None:
            mention = f"<@{user_id}>"
            multiplier, role_name = 0.0, None
        else:
            mention = member.mention
            multiplier, role_name = get_role_multiplier(member)

        salary = total * multiplier
        percentage = int(multiplier * 100)

        if role_name is None:
            role_display = "Χωρίς ρόλο"
        else:
            role_display = f"{role_name} ({percentage}%)"

        lines.append(
            f"👤 {mention}\n"
            f"   🧾 Σύνολο αποδείξεων: **{total:.2f}{CURRENCY_SYMBOL}**\n"
            f"   🏅 Ρόλος: **{role_display}**\n"
            f"   💰 Τελικός μισθός: **{salary:.2f}{CURRENCY_SYMBOL}**\n"
        )

    await channel.send("\n".join(lines))

    # Καθαρίζουμε την εβδομάδα για νέο κύκλο
    db_clear_current_week()


# ================== COMMANDS ==================

@bot.command(name="status")
async def status_command(ctx: commands.Context):
    """
    Δείχνει στον χρήστη το τρέχον εβδομαδιαίο του σύνολο και εκτίμηση μισθού.
    """
    monday_iso, friday_iso = get_current_week_range()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""
        SELECT SUM(value) FROM contributions
        WHERE user_id = ? AND date BETWEEN ? AND ?
    """, (str(ctx.author.id), monday_iso, friday_iso))
    row = c.fetchone()
    conn.close()

    total = row[0] if row and row[0] is not None else 0.0

    multiplier, role_name = get_role_multiplier(ctx.author)
    percentage = int(multiplier * 100)
    salary_estimate = total * multiplier

    if role_name is None:
        role_display = "Χωρίς ρόλο"
    else:
        role_display = f"{role_name} ({percentage}%)"

    await ctx.send(
        f"{ctx.author.mention}\n"
        f"🧾 Τρέχον εβδομαδιαίο σύνολο: **{total:.2f}{CURRENCY_SYMBOL}**\n"
        f"🏅 Ρόλος: **{role_display}**\n"
        f"💰 Εκτίμηση μισθού: **{salary_estimate:.2f}{CURRENCY_SYMBOL}**"
    )


def is_admin(ctx: commands.Context):
    return ctx.author.guild_permissions.manage_guild or ctx.author.guild_permissions.administrator


@bot.command(name="payout_now")
async def payout_now_command(ctx: commands.Context):
    """
    Χειροκίνητο payout (μόνο για admins).
    """
    if not is_admin(ctx):
        await ctx.send("❌ Μόνο άτομα με δικαίωμα **Manage Server** μπορούν να τρέξουν αυτή την εντολή.")
        return

    await run_payout(automatic=False, ctx=ctx)
    # Σημειώνουμε ότι έγινε payout σήμερα
    db_set_setting("last_payout_date", date.today().isoformat())
    await ctx.send("✅ Έγινε χειροκίνητη πληρωμή και καθαρίστηκαν τα δεδομένα εβδομάδας.")


@bot.command(name="reset_week")
async def reset_week_command(ctx: commands.Context):
    """
    Διαγράφει τα δεδομένα της τρέχουσας εβδομάδας (μόνο admins).
    """
    if not is_admin(ctx):
        await ctx.send("❌ Μόνο άτομα με δικαίωμα **Manage Server** μπορούν να τρέξουν αυτή την εντολή.")
        return

    db_clear_current_week()
    await ctx.send("♻️ Τα δεδομένα της τρέχουσας εβδομάδας διαγράφηκαν.")


# ================== RUN BOT ==================

if __name__ == "__main__":
    if not DISCORD_TOKEN or DISCORD_TOKEN == "MTQ0MTk1Njg5ODYyNTgxODg1NA.GA_tqx.q3czJTU0Dxv5H_qLSYZ2vZU1BTmnni3___sKfA":
        print("❌ Βάλε το πραγματικό DISCORD TOKEN στην μεταβλητή DISCORD_TOKEN στην αρχή του αρχείου.")
    else:
        bot.run(DISCORD_TOKEN)


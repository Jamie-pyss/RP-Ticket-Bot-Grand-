import discord
from discord.ext import commands, tasks
from discord.ui import Button, View
from discord import Interaction
import json
import os
import asyncio
from datetime import datetime, timedelta

# ---- CONFIG ----

with open("config.json", "r") as f:
    config = json.load(f)

TOKEN = config["token"]
channel_id = config["channel_id"]
roles_to_ping = config["roles_to_ping"]
button_role_ids = config["button_role_ids"]

STATE_FILE = config["state_file"]
COLLECTED_FILE = config["collected_file"]
START_FILE = config["start_file"]

# ---- DISCORD SETUP ----

intents = discord.Intents.default()
intents.guilds = True
intents.members = True

bot = commands.Bot(command_prefix="/", intents=intents)

# ---- JSON Helper ----

def load_json(file):
    if os.path.exists(file):
        with open(file, "r") as f:
            try:
                return json.load(f)
            except:
                return {}
    return {}

def save_json(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=2)

# ---- LOGGING ----

def log_collection(user, start_hour):
    entry = {
        "DiscordName": str(user),
        "DiscordID": str(user.id),
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "StartHour": start_hour
    }
    data = load_json(COLLECTED_FILE)
    if not isinstance(data, list):
        data = []
    data.append(entry)
    save_json(COLLECTED_FILE, data)

def log_winner(user, start_hour):
    data = load_json(START_FILE)
    current_hour_key = datetime.now().strftime("%Y-%m-%d_%H")
    if str(start_hour) not in data:
        data[str(start_hour)] = {}
    data[str(start_hour)][current_hour_key] = {
        "DiscordName": str(user),
        "DiscordID": str(user.id),
        "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    save_json(START_FILE, data)

# ---- HELPER ----

def has_button_role(member: discord.Member) -> bool:
    return any(r.id in button_role_ids for r in getattr(member, "roles", []))

active_reminders = {}
last_confirm = {}
skip_until_next_hour = {}
last_winner_press = {}

# ---- UI ----

class RPView(View):
    def __init__(self):
        super().__init__(timeout=None)
        for label in ["10 Uhr", "16 Uhr", "22 Uhr"]:
            btn = Button(label=label, style=discord.ButtonStyle.primary, custom_id=f"time_{label}")
            btn.callback = self.time_button_callback
            self.add_item(btn)

    async def time_button_callback(self, interaction: Interaction):
        if not has_button_role(interaction.user):
            await interaction.response.send_message("Du darfst diesen Button nicht benutzen!", ephemeral=True)
            return

        label = interaction.data["custom_id"].replace("time_", "")
        hour = int(label.split(" ")[0])
        current_hour_key = datetime.now().strftime("%Y-%m-%d_%H")

        data = load_json(START_FILE)
        if str(hour) in data and current_hour_key in data[str(hour)]:
            existing_winner = data[str(hour)][current_hour_key]["DiscordID"]
            if existing_winner == str(interaction.user.id):
                await interaction.response.send_message("Du hast in dieser Stunde bereits gewonnen!", ephemeral=True)
                return
            else:
                await interaction.response.send_message("Für diese Stunde wurde bereits ein Gewinner festgelegt!", ephemeral=True)
                return

        log_winner(interaction.user, hour)
        last_winner_press[hour] = current_hour_key

        await interaction.response.send_message(f"🏆 {interaction.user.mention} hat **{label} gewonnen!**", ephemeral=False)

        start_hour = hour
        end_hour = {10: 16, 16: 22, 22: 4}[hour]

        if start_hour in active_reminders:
            try:
                active_reminders[start_hour].cancel()
            except:
                pass

        task = asyncio.create_task(start_reminder(interaction.guild, start_hour, end_hour))
        active_reminders[start_hour] = task

# ---- UI Confirm ----

class ConfirmView(View):
    def __init__(self, start_hour):
        super().__init__(timeout=None)
        btn = Button(label="Eingesammelt", style=discord.ButtonStyle.success, custom_id=f"confirm_{start_hour}")
        btn.callback = self.confirm_callback
        self.add_item(btn)
        self.start_hour = start_hour

    async def confirm_callback(self, interaction: Interaction):
        if not has_button_role(interaction.user):
            await interaction.response.send_message("Du darfst diesen Button nicht benutzen!", ephemeral=True)
            return

        current_hour_key = datetime.now().strftime("%Y-%m-%d_%H")
        if last_confirm.get(self.start_hour) == current_hour_key:
            await interaction.response.send_message("Für diese Stunde wurde schon eingesammelt!", ephemeral=True)
            return

        last_confirm[self.start_hour] = current_hour_key
        skip_until_next_hour[self.start_hour] = current_hour_key
        log_collection(interaction.user, self.start_hour)

        await interaction.response.send_message(f"{interaction.user.mention} hat eingesammelt!", ephemeral=False)

# ---- REMINDER LOGIK ----

async def start_reminder(guild, start_hour, end_hour):
    channel = guild.get_channel(channel_id)
    if not channel:
        return

    now = datetime.now()
    today = now.date()

    if start_hour == 22 and end_hour == 4:
        end_time = datetime.combine(today + timedelta(days=1), datetime.min.time()).replace(hour=4)
    else:
        end_time = datetime.combine(today, datetime.min.time()).replace(hour=end_hour)

    mentions = " ".join([f"<@&{rid}>" for rid in roles_to_ping])
    await channel.send(f"{mentions} RP Tickets einsammeln!", view=ConfirmView(start_hour))

    current_hour = now.replace(minute=0, second=0, microsecond=0)
    for step in [15, 30, 45]:
        remind_time = current_hour + timedelta(minutes=step)
        if remind_time > now and remind_time < end_time:
            await asyncio.sleep(max(0, (remind_time - datetime.now()).total_seconds()))
            if start_hour not in active_reminders:
                return
            if skip_until_next_hour.get(start_hour) == current_hour.strftime("%Y-%m-%d_%H"):
                break
            await channel.send(f"{mentions} Erinnerung: RP Tickets einsammeln!", view=ConfirmView(start_hour))

    next_hour = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    while next_hour <= end_time:
        wait_time = (next_hour - datetime.now()).total_seconds()
        if wait_time > 0:
            await asyncio.sleep(wait_time)

        if start_hour not in active_reminders:
            return

        current_hour_key = next_hour.strftime("%Y-%m-%d_%H")
        if skip_until_next_hour.get(start_hour) == current_hour_key:
            next_hour += timedelta(hours=1)
            continue

        await channel.send(f"{mentions} RP Tickets einsammeln!", view=ConfirmView(start_hour))

        for step in [15, 30, 45]:
            remind_time = next_hour + timedelta(minutes=step)
            if remind_time < end_time:
                await asyncio.sleep(max(0, (remind_time - datetime.now()).total_seconds()))
                if start_hour not in active_reminders:
                    return
                if skip_until_next_hour.get(start_hour) == current_hour_key:
                    break
                await channel.send(f"{mentions} Erinnerung: RP Tickets einsammeln!", view=ConfirmView(start_hour))

        next_hour += timedelta(hours=1)

    await channel.send(f"{mentions} Letzte Chance! RP Tickets einsammeln!", view=ConfirmView(start_hour))
    if start_hour in active_reminders:
        del active_reminders[start_hour]

# ---- BOT ----

@bot.event
async def on_ready():
    print(f"[READY] Eingeloggt als {bot.user}")
    bot.add_view(RPView())

    channel = bot.get_channel(channel_id)
    state = load_json(STATE_FILE)

    msg_id = state.get("message_id")
    if msg_id:
        try:
            await channel.fetch_message(msg_id)
        except:
            pass
    else:
        msg = await channel.send("**RP Fabrik gewonnen?**", view=RPView())
        state["message_id"] = msg.id
        save_json(STATE_FILE, state)

    try:
        synced = await bot.tree.sync()
        print(f"[SYNC] {len(synced)} Slash-Commands synchronisiert.")
    except Exception as e:
        print(f"[SYNC Fehler] {e}")

    repost_ui.start()

# ---- UI POST ----

@tasks.loop(minutes=1)
async def repost_ui():
    now = datetime.now()
    state = load_json(STATE_FILE)
    repost_times = [(10, 15), (16, 15), (22, 15)]
    last_repost = state.get("last_repost")

    if (now.hour, now.minute) in repost_times:
        today_key = f"{now.date()}_{now.hour}_{now.minute}"
        if last_repost == today_key:
            return
        channel = bot.get_channel(channel_id)
        msg_id = state.get("message_id")
        if msg_id:
            try:
                old_msg = await channel.fetch_message(msg_id)
                if old_msg:
                    await old_msg.delete()
            except:
                pass
        msg = await channel.send("**RP Fabrik gewonnen?**", view=RPView())
        state["message_id"] = msg.id
        state["last_repost"] = today_key
        save_json(STATE_FILE, state)
        print(f"[REPOST] Neue UI Nachricht um {now.strftime('%H:%M')} gepostet.")

# ---- START -----

bot.run(TOKEN)

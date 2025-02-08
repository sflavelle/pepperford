# Import essential libraries
import json
import logging
import os
import subprocess
import signal

import discord
import requests
import yaml
import sqlite3
from contextlib import closing
from discord import app_commands
from discord.ext import commands

# setup logging
logger = logging.getLogger('discord')
handler = logging.StreamHandler()
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# init vars?
cfg = None

# load config
with open('config.yaml', 'r') as file:
    cfg = yaml.safe_load(file)

# Database connections
# Fact DB
with closing(sqlite3.connect("facts.db")) as factdb:
    with closing(factdb.cursor()) as cursor:
        # init table
        cursor.execute("CREATE TABLE IF NOT EXISTS facts (fact TEXT, source TEXT, keyword TEXT)")

# configure subscribed intents
intents = discord.Intents.default()

# setup command framework
splatbot = commands.Bot(
    command_prefix="!",
    intents=intents,
    allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
    allowed_installs=app_commands.AppInstallationType(guild=False, user=True)
)


@splatbot.tree.command()
@app_commands.describe(room_url="Link to the Archipelago room",
                       comment="Additional comment to prefix the room details with",
                       public="Whether to post publically or to yourself",
                       include_log="Include a link to the server log",
                       include_files="Set a link to patch files etc to include in the post",
                       include_games="List out each player's games as well")
async def ap_roomdetails(interaction: discord.Interaction,
                         room_url: str,
                         comment: str = None,
                         public: bool = True,
                         include_log: bool = False,
                         include_files: str = None,
                         include_games: bool = False):
    """Post the details of an Archipelago room to the channel."""

    deferpost = await interaction.response.defer(ephemeral=not public, thinking=True)
    newpost = await interaction.original_response()

    room_id = room_url.split('/')[-1]
    hostname = room_url.split('/')[2]

    match room_url.split('/')[3]:
        case "tracker":
            await newpost.edit(content=f"**:no_entry_sign: You tried!**\n{interaction.user.display_name} gave me a tracker link, "
                                       "but I need a room URL to post room details.")
            raise ValueError

    api_url = f"https://{hostname}/api/room_status/{room_id}"

    room = requests.get(api_url)
    room_json = room.json()

    players = [p[0] for p in room_json['players']]

    # Form message
    msg = ""
    if comment: msg = comment + "\n"
    msg += room_url + "\n"
    if include_games: msg += f"Players: {", ".join(sorted([f"{p[0]} (*{p[1]}*)" for p in room_json['players']]))}"
    else: msg += f"Players: {", ".join(sorted(players))}"
    if bool(include_files): msg += "\n" + f"Patches + Misc Files: {include_files}"
    if include_log: msg += "\n" + f"Server Log: https://{hostname}/log/{room_id}"
    await newpost.edit(content=msg)

ap_itemlog = app_commands.Group(name="ap_itemlog",description="Manage an item logging webhook")
itemlog_processes = {}

@ap_itemlog.command(name="start")
async def ap_itemlog_start(interaction: discord.Interaction, webhook: str, log_url: str, session_cookie: str):
    """Start logging messages from an Archipelago room log to a specified webhook"""
    script_path = os.path.join(os.path.dirname(__file__), 'ap_logwatch.py')

    env = os.environ.copy()
    env['LOG_URL'] = log_url
    env['WEBHOOK_URL'] = webhook
    env['SESSION_COOKIE'] = session_cookie

    process = subprocess.Popen(['python', script_path], env=env)
    itemlog_processes[interaction.guild.id] = process.pid
    await interaction.response.send_message(f"Started logging messages from {log_url} to a webhook. PID: {process.pid}", ephemeral=True)

async def ap_itemlog_stop(interaction: discord.Interaction):
    """Stops the log monitoring script."""
    pid = itemlog_processes.get(interaction.guild.id)
    if pid:
        os.kill(pid, signal.SIGTERM)
        await interaction.response.send_message(f"Stopped log monitoring script with PID: {pid}", ephemeral=True)
        del itemlog_processes[interaction.guild.id]
    else:
        await interaction.response.send_message("No log monitoring script is currently running.", ephemeral=True)

ap_spoilers = app_commands.Group(name='ap_spoil',description='Spoil something for an Archipelago game')

@ap_spoilers.command(name="item")
@app_commands.describe(seed="Archipelago seed URL")
async def ap_spoil(interaction: discord.Interaction,
                   seed_url: str,
                   player: str,
                   item: str):

    seed_id = seed_url.split('/')[-1]
    hostname = seed_url.split('/')[2]

@splatbot.tree.command()
@app_commands.describe(command="Command to send to Home Assistant")
async def home(interaction: discord.Interaction, command: str):
    """Send a message to Home Assistant's Assist API, eg 'turn on the lights'."""

    global cfg

    api_url = cfg['hass']['url'] if cfg['hass'] else None
    api_token = cfg['hass']['token'] if cfg['hass'] else None

    if api_url is None or api_token is None:
        await interaction.response.send_message(
            ":no_entry_sign: Tell the bot owner to configure their Home Assistant API's URL and Long Lived Token.",
            ephemeral=True)
        raise NotImplementedError("API credentials not configured")

    api_headers = {
        "Authorization": f"Bearer {api_token}",
        "content-type": "application/json"
    }

    sentreq = {
        "text": command,
        "language": "en"
    }

    req = requests.post(
        f"{api_url}/api/conversation/process",
        headers=api_headers,
        data=json.dumps(sentreq)
    )
    response = req.json()
    # if response.status_code == requests.codes.ok:
    recvtext = response["response"]["speech"]["plain"]["speech"]
    await interaction.response.send_message(f"> {command}\n{recvtext}", ephemeral=True)

factgroup = app_commands.Group(name='facts',description='Some totally legit facts')

@factgroup.command(name="get")
async def fact_get(interaction: discord.Interaction, public: bool = True):
    """Post a totally legitimate factoid"""
    with closing(sqlite3.connect("facts.db")) as factdb:
        with closing(factdb.cursor()) as cursor:
            fact = cursor.execute('SELECT * from facts order by random() limit 1').fetchall()[0]
            if len(fact) == 0:
                await interaction.response.send_message(":no_entry_sign: No facts available!")
                return

    template = """**Fact:** {factstr}
-# Source: <{source}>
-# Disclaimer: Facts reported by this command are not factual, informative, or any combination of the prior."""

    print(fact)

    await interaction.response.send_message(template.format(
        factstr=fact[0],
        source=fact[1]
    ), ephemeral = not public)

@factgroup.command(name="add")
async def fact_get(interaction: discord.Interaction, fact: str, keyword: str, source: str = "no source"):
    """Add a fact to the database"""
    row = (fact, source, keyword)
    with closing(sqlite3.connect("facts.db")) as factdb:
        with closing(factdb.cursor()) as cursor:
            cursor.execute(f"insert into facts values (?,?,?)", row)
            factdb.commit()
            if bool(cursor.lastrowid):
                await interaction.response.send_message(":white_check_mark: Added successfully. "
                    f"Items: {cursor.lastrowid + 1}", ephemeral=True)

splatbot.tree.add_command(factgroup)
splatbot.tree.add_command(ap_itemlog)

@splatbot.event
async def on_ready():
    logger.info(f"Logged in. I am {splatbot.user} (ID: {splatbot.user.id})")
    await splatbot.tree.sync()


splatbot.run(cfg['bot']['discord_token'],
             log_handler=handler
             )

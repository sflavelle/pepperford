import json
import os
import sys
import subprocess
import requests
import logging
import yaml
import traceback
import typing
from io import BytesIO
import psycopg2 as psql
from psycopg2.extras import Json as psql_json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from tabulate import tabulate
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands._types import BotT

# from cmds.ap_scripts.archilogger import ItemLog
from cmds.ap_scripts.emitter import event_emitter

cfg = None

logger = logging.getLogger('discord.ap')

with open('config.yaml', 'r', encoding='UTF-8') as file:
    cfg = yaml.safe_load(file)

sqlcfg = cfg['bot']['archipelago']['psql']
try: 
    sqlcon = psql.connect(
        dbname=sqlcfg['database'],
        user=sqlcfg['user'],
        password=sqlcfg['password'] if 'password' in sqlcfg else None,
        host=sqlcfg['host'],
    )
    sqlcon.set_session(autocommit=True)
except psql.OperationalError:
    # TODO Disable commands that need SQL connectivity
    sqlcon = False

class Archipelago(commands.GroupCog, group_name="archipelago"):
    """Commands relating to the Archipelago randomizer"""

    def __init__(self, bot):
        self.ctx = bot


    async def cog_command_error(self, ctx: Context[BotT], error: Exception) -> None:
        await ctx.reply(f"Command error: {error}",ephemeral=True)

    @app_commands.command()
    @app_commands.describe(room_url="Link to the Archipelago room",
                           comment="Additional comment to prefix the room details with",
                           public="Whether to post publically or to yourself",
                           include_files="Set a link to patch files etc to include in the post",
                           include_games="List out each player's games as well")
    async def roomdetails(self, interaction: discord.Interaction,
                             room_url: str,
                             comment: str = None,
                             public: bool = True,
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

        room = requests.get(api_url,timeout=5)
        room_json = room.json()

        players = [p[0] for p in room_json['players']]

        # Form message
        msg = ""
        if comment: msg = comment + "\n"
        msg += room_url + "\n"
        if bool(include_files): msg += f"Patches + Misc Files: {include_files}\n"
        if include_games:
            msg += f"Players:\n{"\n".join(sorted([f"**{p[0]}**: {p[1]}" for p in room_json['players']]))}"
        else:
            msg += f"Players: {", ".join(sorted(players))}"
        await newpost.edit(content=msg)

    db = app_commands.Group(name="db",description="Query the bot's Archipelago database")

    # First some helpers
    async def db_table_complete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        cursor = sqlcon.cursor()
        cursor.execute("select tablename from pg_catalog.pg_tables where schemaname = 'public'")
        response = cursor.fetchall()
        return [app_commands.Choice(name=opt[0],value=opt[0]) for opt in response]
    
    async def db_game_complete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        cursor = sqlcon.cursor()
        cursor.execute("select game, count(*) from item_classification group by game;")
        response = sorted([opt[0] for opt in cursor.fetchall()])
        if len(current) == 0:
            return [app_commands.Choice(name=opt,value=opt) for opt in response[:20]]
        else:
            return [app_commands.Choice(name=opt,value=opt) for opt in response if current in opt][:20]
    
    async def db_item_complete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        cursor = sqlcon.cursor()
        game_selection = ctx.data['options'][0]['options'][0]['options'][0]['value']
        cursor.execute(f"select item from item_classification where game = '{str(game_selection)}';")
        response = sorted([opt[0] for opt in cursor.fetchall()])
        if len(current) == 0:
            return [app_commands.Choice(name=opt,value=opt) for opt in response[:20]]
        elif "%" in current or "?" in current:
            return [app_commands.Choice(name=f"{current} (Multi-Selection)",value=current)]
        else:
            return [app_commands.Choice(name=opt,value=opt) for opt in response if current in opt][:20]
        
    async def db_location_complete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        cursor = sqlcon.cursor()
        game_selection = ctx.data['options'][0]['options'][0]['options'][0]['value']
        cursor.execute(f"select location from game_locations where game = '{str(game_selection)}';")
        response = sorted([opt[0] for opt in cursor.fetchall()])
        if len(current) == 0:
            return [app_commands.Choice(name=opt,value=opt) for opt in response[:20]]
        elif "%" in current or "?" in current:
            return [app_commands.Choice(name=f"{current} (Multi-Selection)",value=current)]
        else:
            return [app_commands.Choice(name=opt,value=opt) for opt in response if current in opt][:20]
    
    async def db_classification_complete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        permitted_values = [
            "progression", # Unlocks new checks
            "conditional progression", # Progression overall, but maybe only in certain settings or certain qualities
            "useful", # Good to have but doesn't unlock anything new
            "currency", # Filler, but specifically currency
            "filler", # Filler - not really necessary
            "trap" # Negative effect upon the player
            ]
        if len(current) == 0:
            return [app_commands.Choice(name=opt.title(),value=opt) for opt in permitted_values]
        else:
            return [app_commands.Choice(name=opt.title(),value=opt) for opt in permitted_values if current in opt]

    @commands.is_owner()
    @db.command(name='select')
    @app_commands.describe(table="The table to select from",
                           selection="What columns/functions to select (* for all)",
                           where="specify a WHERE filter",
                           public="publish the result?")
    @app_commands.autocomplete(table=db_table_complete)
    async def db_select(self, interaction: discord.Interaction, table: str, selection: str, where: str = None, public: bool = False):
        """Run a basic PostgreSQL SELECT command on a table."""

        cursor = sqlcon.cursor()
        logger.info(f"executed SQL command from discord: SELECT {selection} FROM {table} {f'WHERE {where}' if bool(where) else ''};")
        cursor.execute(f"SELECT {selection} FROM {table} {f'WHERE {where}' if bool(where) else ''};")
        response = cursor.fetchall()

        # Set headers (for prettiness)
        headers = [desc[0].replace("_", " ").title() for desc in cursor.description]
        
        str_response = tabulate(response,headers=headers)
        try: 
            await interaction.response.send_message(str_response,ephemeral=not public)
        except discord.errors.HTTPException:
            responsefile = bytes(str_response,encoding='UTF-8')
            await interaction.response.send_message("Here's the result, as a file:",file=discord.File(BytesIO(responsefile), 'result.txt'),ephemeral=not public)

    @commands.is_owner()
    @db.command(name='update_item_classification')
    @app_commands.describe(game="The game that contains the item",
                           item="The item to act on (wildcards: ? one, % many)",
                           classification="The item's importance")
    @app_commands.autocomplete(game=db_game_complete,item=db_item_complete,classification=db_classification_complete)
    async def db_update_item_classification(self, interaction: discord.Interaction, game: str, item: str, classification: str):
        """Update the classification of an item."""
        cursor = sqlcon.cursor()

        if '%' in item:
            cursor.execute("UPDATE item_classification SET classification = %s where game = %s and item like %s", (classification.lower(), game, item))
            count = cursor.rowcount
            logger.info(f"Classified {str(count)} item(s) matching '{item}' in {game} to {classification}")
            return await interaction.response.send_message(f"Classification for {game}'s {str(count)} items matching '{item}' was successful.",ephemeral=True)
        else: 
            try:
                cursor.execute("UPDATE item_classification SET classification = %s where game = %s and item = %s", (classification.lower(), game, item))
                logger.info(f"Classified '{item}' in {game} to {classification}")
                return await interaction.response.send_message(f"Classification for {game}'s '{item}' was successful.",ephemeral=True)
            finally:
                pass

    @commands.is_owner()
    @db.command(name='update_location_checkability')
    @app_commands.describe(game="The game that contains the location",
                           location="The location to act on (wildcards: ? one, % many)",
                           is_checkable="Can the location be checked by a player?")
    @app_commands.autocomplete(game=db_game_complete,location=db_location_complete)
    async def db_update_location_checkability(self, interaction: discord.Interaction, game: str, location: str, is_checkable: bool):
        """Update the checkability of a game's location. Non-checkable locations are classified as Events in Archipelago."""
        cursor = sqlcon.cursor()

        if '%' in location:
            cursor.execute("UPDATE game_locations SET is_checkable = %s where game = %s and location like %s", (is_checkable, game, location))
            count = cursor.rowcount
            logger.info(f"Classified {str(count)} locations(s) matching '{location}' in {game} to {'not ' if is_checkable is False else ''}checkable")
            return await interaction.response.send_message(f"Classification for {game}'s {str(count)} locations matching '{location}' was successful.",ephemeral=True)
        else: 
            try:
                cursor.execute("UPDATE game_locations SET is_checkable = %s where game = %s and location = %s", (is_checkable, game, location))
                logger.info(f"Classified '{location}' in {game} to {'not ' if is_checkable is False else ''}checkable")
                return await interaction.response.send_message(f"Classification for {game}'s '{location}' was successful.",ephemeral=True)
            finally:
                pass

    @db.command()
    async def import_datapackage(self, interaction: discord.Interaction, url: str = "https://archipelago.gg/datapackage"):
        """Import items and locations from an Archipelago datapackage into the database."""

        with sqlcon.cursor() as cursor:

            deferpost = await interaction.response.defer(ephemeral=True, thinking=True,)
            newpost = await interaction.original_response()

            data = requests.get(url, timeout=5)
            datapackage = data.json()

            games = list(datapackage['games'].keys())
            games.remove("Archipelago") # Skip the Archipelago data

            msg = f"The datapackage provided has data for:\n\n{", ".join(games)}\n\nImport in progress..."
            if len(msg) > 2000:
                msg = f"The datapackage provided has data for {len(games)} games. Import in progress..."
            await newpost.edit(content=msg)

            for game, data in datapackage['games'].items():
                if game == "Archipelago": continue
                for item in data['item_name_groups']['Everything']:
                    logger.info(f"Importing {game}: {item} to item_classification")
                    cursor.execute(
                        "INSERT INTO item_classification (game, item, classification) VALUES (%s, %s, %s) ON CONFLICT (game, item) DO NOTHING;",
                        (game, item, None))
                for location in data['location_name_groups']['Everywhere']:
                    logger.info(f"Importing {game}: {location} to game_locations")
                    # Any location that shows up in the datapackage appears to be checkable
                    cursor.execute(
                        "INSERT INTO game_locations (game, location, is_checkable) VALUES (%s, %s, %s) ON CONFLICT (game, location) DO UPDATE SET is_checkable = EXCLUDED.is_checkable;",
                        (game, location, True))
                await newpost.edit(content=f"Imported {game}...")

        return await newpost.edit(content="Import *should* be complete!")
    
    aproom = app_commands.Group(name="room",description="Commands to do with the current room")
    
    async def link_slot_complete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        permitted_values = self.ctx.extras['ap_rooms'][ctx.guild_id]['players']
        if len(current) == 0:
            return [app_commands.Choice(name=opt.title(),value=opt) for opt in permitted_values]
        else:
            return [app_commands.Choice(name=opt.title(),value=opt) for opt in permitted_values if current in opt]

    @aproom.command()
    @app_commands.autocomplete(slot_name=link_slot_complete)
    async def link_slot(self, interaction: discord.Interaction, slot_name: str, user: discord.User = None):
        """Link an Archipelago slot name to your Discord account."""

        if user is None:
            user = interaction.user

        cmd = "UPDATE games.players SET discord_user = %s WHERE player_name = %s"
        with sqlcon.cursor() as cursor:
            cursor.execute(cmd, (user.id, slot_name))
            # sqlcon.commit()

        return await interaction.response.send_message(f"Linked {slot_name} to {user.display_name} ({user.id})!",ephemeral=True)

    @aproom.command()
    async def set_room(self, interaction: discord.Interaction, room_url: str):
        """Set the current Archipelago room for this server. Will affect other commands."""
        
        logger.info(f"Setting room for {interaction.guild.name} ({interaction.guild.id}) to {room_url}...")

        deferpost = await interaction.response.defer(ephemeral=True, thinking=True,)
        newpost = await interaction.original_response()

        if room_url.split('/')[-2] != "room":
            return await newpost.edit(content="**Error**: the provided URL is not an Archipelago room URL.",delete_after=15.0)

        room_id = room_url.split('/')[-1]
        hostname = room_url.split('/')[2]

        api_url = f"https://{hostname}/api/room_status/{room_id}"

        logger.info(f"Fetching room data from {api_url}...")
        try:
            room = requests.get(api_url, timeout=5)
        except requests.exceptions.Timeout:
            return await newpost.edit(content="**Error**: the provided URL is not responding. Please check the URL and try again.",delete_after=15.0)
        except requests.exceptions.RequestException as e:
            logger.error(f"Error fetching room data: {e}")
            return await newpost.edit(content="**Error**: there was a problem fetching the room data. Please try again later.",delete_after=15.0)

        api_data = requests.get(api_url, timeout=5).json()
        logger.info("Fetched room data from API...")

        room_port = api_data['last_port']

        players = []
        for p in api_data['players']:
            players.append(p[0])
        
        with sqlcon.cursor() as cursor:
            commands = [
                (
                    '''INSERT INTO games.all_rooms
                    (room_id, guild, active, host, port, players)
                    VALUES (%s, %s, %s, %s, %s, %s);''',
                    (room_id, interaction.guild_id, 'true', hostname, room_port, players)
                ),
                (
                    '''UPDATE games.all_rooms
                    SET active = %s
                    WHERE room_id != %s AND guild = %s;''',
                    ('false', room_id, interaction.guild_id)
                ),
            ]
            for p in players:
                commands.append((
                    '''INSERT INTO games.players
                    (player_name)
                    VALUES (%s);''',
                    (p,)
                ))

            # When we're ready
            for command in commands:
                logger.info(f"Executing SQL: {command[0]} with {command[1]}")
                cmd, params = command
                cursor.execute(cmd, params)

        logger.info("SQL commands executed.")
        logger.info("Setting up room data...")
        self.ctx.extras['ap_rooms'] = {}
        self.ctx.extras['ap_rooms'][interaction.guild_id] = {
            'room_id': room_id,
            'hostname': hostname,
            'room_port': room_port,
            'players': players,
        }
        logger.info(f"Set room for {interaction.guild.name} ({interaction.guild.id}) to {room_url}")
        await newpost.edit(content=f"Set room for {interaction.guild.name} to {room_url}!")

    @aproom.command()
    async def get_hints(self, interaction: discord.Interaction):
        """Get hints for the current room."""

        deferpost = await interaction.response.defer(ephemeral=True, thinking=True,)
        newpost = await interaction.original_response()

        if not self.ctx.extras.get('ap_rooms'):
            return await newpost.edit(content="No Archipelago room is currently set for this server.")

        room = self.ctx.extras['ap_rooms'].get(interaction.guild_id)
        if not room:
            return await newpost.edit(content="No Archipelago room is currently set for this server.")

        room_slots = requests.get(f"https://{room['hostname']}/api/room_status/{room['room_id']}", timeout=10).json()['players']

        linked_slots = []
        with sqlcon.cursor() as cursor:
            cursor.execute("SELECT player_name FROM games.room_players WHERE room_id = %s AND guild = %s;", (room['room_id'], interaction.guild_id))
            linked_slots = [row[0] for row in cursor.fetchall() if row[0] in room_slots]
        if len(linked_slots) == 0:
            return await newpost.edit(content="None of your Archipelago slots are linked to this game.")

        # Get the game table
        game_table = requests.get(f"http://localhost:42069/inspectgame", timeout=10).json()

        # Build the hint table
        hint_table = {}
        for slot in linked_slots:
            if slot in game_table['players']:
                hint_table[slot] = {
                    h.location: {"item": h.item,
                                 "receiver": h.receiver,
                                 "classification": h.classification,
                                 "entrance": h.location_entrance,
                                } for h in game_table['players'][slot].hints if h.found is False}
                
        # Format the hint table
        hint_table_list = []
        for slot, hints in hint_table.items():
            for location, details in hints.items():
                hint_table_list.append({
                    "Slot": slot,
                    "Item": details["item"],
                    "Location": location,
                    "Entrance": details["entrance"],
                    "Receiver": details["receiver"],
                    "Classification": details["classification"],
                })
        
        hint_table_str = tabulate(hint_table_list, headers="keys", tablefmt="grid")
        if len(hint_table_str) > 2000:
            hint_table_str = f"Hint table too long ({len(hint_table_str)} characters). Sending as a file."
            hint_table_file = bytes(hint_table_str, encoding='UTF-8')
            hint_table_str = "Here's the hint table, as a file:"
            await newpost.edit(content=hint_table_str, file=discord.File(BytesIO(hint_table_file), 'hints.txt'))
        else:
            await newpost.edit(content=hint_table_str)


    itemlogging = app_commands.Group(name="itemlog",description="Manage an item logging webhook")

    """ (2025-03-15)
    Hooo boy, okay, so:
    What I would like to do here is run the AP Game Monitoring script in such a way that:
    - other commands can retrieve info from it
    - once started, it can run in the background without interrupting or blocking the main bot loop

    Unfortunately, I haven't found the way to do this yet.
    Before rewriting the main guts of the bot this is attached to (to make things like THIS more modular),
    this was running as a separate script, which works great, but (at present) doesn't have IPC/whatever written in.
    That may be a path I need to take: multiprocessing or multithreading, or straight async doesn't have the desired result.
    I was looking at ZeroMQ to begin with before looking at built-in libraries, but we'll just need to see...

    All this to say, at present:
    - The CREATION of the itemlog works (it should be a self-contained class now)
    - Running it DOES NOT work
    """

    @itemlogging.command()
    @app_commands.describe(log_channel="Channel or thread to post the item log into",
                           log_url="The Archipelago room's log page (room url also works)",
                           spoiler_url="The Archipelago seed's spoiler URL (seed url also works)",
                           chat_channel="Specify a channel or thread here to receive chat messages from AP")
    async def create(self, interaction: discord.Interaction, log_channel: discord.TextChannel|discord.Thread, log_url: str, spoiler_url: str = None, chat_channel: discord.TextChannel|discord.Thread = None):
        """Start logging messages from an Archipelago room log to a specified webhook"""

        deferpost = await interaction.response.defer(ephemeral=True, thinking=True,)
        newpost = await interaction.original_response()

        if interaction.guild_id in self.ctx.procs['archipelago']:
            if isinstance(self.ctx.procs['archipelago'][interaction.guild_id], ItemLog):
                return await newpost.edit(content=f"We've already got an itemlog configured for this guild.")
        # env = os.environ.copy()
        # env['LOG_URL'] = log_url
        # env['WEBHOOK_URL'] = webhook
        # env['SESSION_COOKIE'] = cfg['bot']['archipelago']['session_cookie']
        # env['SPOILER_URL'] = spoiler_url if spoiler_url else None
        # env['MSGHOOK_URL'] = log['msghook'] if log['msghook'] else None

        ping_log = requests.get(log_url, cookies={'session': cfg['bot']['archipelago']['session_cookie']}, timeout=3)
        if ping_log.status_code == 200:
            # All checks successful, start the script
            # process = subprocess.Popen(['python', script_path], env=env)
            try:
                status = ""
                loop = asyncio.get_event_loop()

                self.ctx.procs['archipelago'][interaction.guild_id] = ItemLog(
                    self.ctx,
                    interaction.guild,
                    log_url,
                    log_channel,
                    cfg['bot']['archipelago']['session_cookie'],
                    spoiler_url,
                    chat_channel,
                    # TODO Check if the thread needs to be specified here
                )
                logger.info("Successfully created ItemLog object")
                status = "Item log creation successful!"

                if bool(self.ctx.procs['archipelago'][interaction.guild_id].seed_id):
                    status += "\nParsing the spoiler log... <a:netscape:1349566699766284340>"
                await newpost.edit(content=status)
                spoiler_parse = await loop.run_in_executor(ThreadPoolExecutor(), self.ctx.procs['archipelago'][interaction.guild_id].parse_spoiler_log())
                await spoiler_parse
                if spoiler_parse is False:
                    await newpost.edit(content=f"{status}\nThere was a problem parsing the spoiler log.")
                    return False
                else:
                    logger.info("Initialisation successful.")
                    if bool(self.ctx.procs['archipelago'][interaction.guild_id].seed_id):
                        status = status.replace(" <a:netscape:1349566699766284340>","")
                        status += "\nParsed spoiler successfully!"
                        await newpost.edit(content=status)

                await newpost.edit(content="Item log successfully initialised!\n-# Please note that when you start " 
                                   "the item log for the time time, it may take a long time " 
                                   "for the first messages to show up - depending on the size of the current log and " 
                                   "connection to the item classification database.")

                # Save script to config
                if 'itemlogs' not in cfg['bot']['archipelago']:
                    cfg['bot']['archipelago']['itemlogs'] = []
                if not any([obj['guild'] == interaction.guild.id for obj in cfg['bot']['archipelago']['itemlogs']]):
                    cfg['bot']['archipelago']['itemlogs'].append({
                        'guild': interaction.guild.id,
                        'channel': log_channel.id,
                        'log_url': log_url,
                        'spoiler_url': spoiler_url if spoiler_url else None,
                    })

                with open('config.yaml', 'w') as file:
                    yaml.dump(cfg, file)
                    logger.info(f"Saved AP log {log_url} to config.")
            except BaseException as error:
                tb = traceback.format_exc()
                logger.error(tb)
                await newpost.edit(content=f"{status}\nThere was a problem executing:\n```{error}\n{tb}```")
            finally:
                pass
        else:
            await newpost.edit(content=f"Could not validate {log_url}: Status code {ping_log.status_code}. {"You'll need your session cookie from the website." if ping_log.status_code == 403 else ""}")

    @itemlogging.command()
    async def start(self, interaction: discord.Interaction):
        """Starts a configured log monitoring script."""
        logger.debug(self.ctx.procs['archipelago'])
        loop = asyncio.get_event_loop()
        itemlog = self.ctx.procs['archipelago'].get(interaction.guild_id)
        if isinstance(itemlog, ItemLog):
            await loop.run_in_executor(ThreadPoolExecutor(), self.ctx.procs['archipelago'][interaction.guild_id].main_loop.start())
            await interaction.response.send_message("Now running item log monitor.", ephemeral=True)
        else:
            await interaction.response.send_message("No log monitoring script is currently configured in this guild.", ephemeral=True)

    @itemlogging.command()
    async def stop(self, interaction: discord.Interaction):
        """Stops the log monitoring script."""
        itemlog = self.ctx.procs['archipelago'].get(interaction.guild_id)
        if itemlog:
            itemlog.main_loop.stop()
            await interaction.response.send_message(f"Stopped log monitoring script.", ephemeral=True)
        else:
            await interaction.response.send_message("No log monitoring script is currently running.", ephemeral=True)

    # @ap_itemlog_stop.autocomplete('guild')
    # async def itemlog_get_running(interaction: discord.Interaction, current: int) -> list[app_commands.Choice[int]]:
    #     choices = [scr['guild'] for scr in cfg['bot']['archipelago']['itemlogs']]
    #     return [
    #         app_commands.Choice(name=str(choice), value=choice)
    #         for choice in choices
    #     ]

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.ctx.procs.get('archipelago'):
            self.ctx.procs['archipelago'] = {}
        # self.ctx.extras['ap_channel'] = next((chan for chan in self.ctx.spotzone.text_channels if chan.id == 1163808574045167656))
        # while testing
        # self.ctx.extras['ap_channel'] = self.ctx.fetch_channel(1349546289490034821)
        # self.ctx.extras['ap_webhook'] = await self.ctx.extras['ap_channel'].webhooks()
        # if len(self.ctx.extras['ap_webhook']) == 1: self.ctx.extras['ap_webhook'] = self.ctx.extras['ap_webhook'][0]

            # Run itemlogs if any are configured
        if len(cfg['bot']['archipelago']['itemlogs']) > 0:
            logger.info("Starting saved itemlog processes.")
            for log in cfg['bot']['archipelago']['itemlogs']:
                logger.info(f"Starting itemlog for guild ID {log['guild']}")
                logger.info(f"Info: {json.dumps(log)}")
                env = os.environ.copy()
            
                env['LOG_URL'] = log['log_url']
                env['WEBHOOK_URL'] = log['webhook']
                env['SESSION_COOKIE'] = cfg['bot']['archipelago']['session_cookie']
                env['SPOILER_URL'] = log['spoiler_url'] if log['spoiler_url'] else None
                env['MSGHOOK_URL'] = log['msghook'] if log['msghook'] else None
            
                try: 
                    script_path = os.path.join(os.path.dirname(__file__), '..', 'ap_itemlog.py')
                    process = subprocess.Popen([sys.executable, script_path], env=env)
                    self.ctx.procs['archipelago'][log['guild']] = process.pid
                except:
                    logger.error("Error starting log:",exc_info=True)

async def setup(bot):
    logger.info("Loading Archipelago cog extension.")
    await bot.add_cog(Archipelago(bot))

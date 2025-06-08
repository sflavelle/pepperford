import json
import os
import sys
import subprocess
import requests
import logging
import signal
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
        port=sqlcfg['port']
    )
    sqlcon.set_session(autocommit=True)
except psql.OperationalError:
    # TODO Disable commands that need SQL connectivity
    sqlcon = False

def join_words(words):
    if len(words) > 2:
        return '%s, and %s' % ( ', '.join(words[:-1]), words[-1] )
    elif len(words) == 2:
        return ' and '.join(words)
    else:
        return words[0]
    
def is_aphost():
    async def predicate(ctx):
        return ctx.user.get_role(1234064646491602944) is not None
    return commands.check(predicate)

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
                await newpost.edit(
                    content=f"**:no_entry_sign: You tried!**\n{interaction.user.display_name} gave me a tracker link, "
                    "but I need a room URL to post room details."
                )
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
        cursor.execute("select game, count(*) from archipelago.item_classifications group by game;")
        response = sorted([opt[0] for opt in cursor.fetchall()])
        if len(current) == 0:
            return [app_commands.Choice(name=opt,value=opt) for opt in response[:20]]
        else:
            return [app_commands.Choice(name=opt,value=opt) for opt in response if current.lower() in opt.lower()][:20]

    async def db_item_complete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        cursor = sqlcon.cursor()
        game_selection = ctx.data['options'][0]['options'][0]['options'][0]['value']
        cursor.execute(f"select item from archipelago.item_classifications where game = '{str(game_selection)}';")
        response = sorted([opt[0] for opt in cursor.fetchall()])
        if len(current) == 0:
            return [app_commands.Choice(name=opt,value=opt) for opt in response[:20]]
        elif "%" in current or "?" in current:
            return [app_commands.Choice(name=f"{current} (Multi-Selection)",value=current)]
        else:
            return [app_commands.Choice(name=opt,value=opt) for opt in response if current.lower() in opt.lower()][:20]

    async def db_location_complete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        cursor = sqlcon.cursor()
        game_selection = ctx.data['options'][0]['options'][0]['options'][0]['value']
        cursor.execute(f"select location from archipelago.game_locations where game = '{str(game_selection)}';")
        response = sorted([opt[0] for opt in cursor.fetchall()])
        if len(current) == 0:
            return [app_commands.Choice(name=opt,value=opt) for opt in response[:20]]
        elif "%" in current or "?" in current:
            return [app_commands.Choice(name=f"{current} (Multi-Selection)",value=current)]
        else:
            return [app_commands.Choice(name=opt,value=opt) for opt in response if current.lower() in opt.lower()][:20]

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
            return [app_commands.Choice(name=opt.title(),value=opt) for opt in permitted_values if current.lower() in opt.lower()]

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

    @is_aphost()
    @db.command(name='update_item_classification')
    @app_commands.describe(game="The game that contains the item",
                           item="The item to act on (wildcards: ? one, % many)",
                           classification="The item's importance")
    @app_commands.autocomplete(game=db_game_complete,item=db_item_complete,classification=db_classification_complete)
    async def db_update_item_classification(self, interaction: discord.Interaction, game: str, item: str, classification: str):
        """Update the classification of an item."""
        cursor = sqlcon.cursor()

        if '%' in item or '?' in item:
            cursor.execute("UPDATE archipelago.item_classifications SET classification = %s where game = %s and item like %s", (classification.lower(), game, item))
            count = cursor.rowcount
            logger.info(f"Classified {str(count)} item(s) matching '{item}' in {game} to {classification}")
            return await interaction.response.send_message(f"Classification for {game}'s {str(count)} items matching '{item}' was successful.",ephemeral=True)
        else:
            try:
                cursor.execute("UPDATE archipelago.item_classifications SET classification = %s where game = %s and item = %s", (classification.lower(), game, item))
                logger.info(f"Classified '{item}' in {game} to {classification}")
                return await interaction.response.send_message(f"Classification for {game}'s '{item}' was successful.",ephemeral=True)
            finally:
                pass

    @is_aphost()
    @db.command(name='update_location_checkability')
    @app_commands.describe(game="The game that contains the location",
                           location="The location to act on (wildcards: ? one, % many)",
                           is_checkable="Can the location be checked by a player?")
    @app_commands.autocomplete(game=db_game_complete,location=db_location_complete)
    async def db_update_location_checkability(self, interaction: discord.Interaction, game: str, location: str, is_checkable: bool):
        """Update the checkability of a game's location. Non-checkable locations are classified as Events in Archipelago."""
        cursor = sqlcon.cursor()

        if '%' in location:
            cursor.execute("UPDATE archipelago.game_locations SET is_checkable = %s where game = %s and location like %s", (is_checkable, game, location))
            count = cursor.rowcount
            logger.info(f"Classified {str(count)} locations(s) matching '{location}' in {game} to {'not ' if is_checkable is False else ''}checkable")
            return await interaction.response.send_message(f"Classification for {game}'s {str(count)} locations matching '{location}' was successful.",ephemeral=True)
        else:
            try:
                cursor.execute("UPDATE archipelago.game_locations SET is_checkable = %s where game = %s and location = %s", (is_checkable, game, location))
                logger.info(f"Classified '{location}' in {game} to {'not ' if is_checkable is False else ''}checkable")
                return await interaction.response.send_message(f"Classification for {game}'s '{location}' was successful.",ephemeral=True)
            finally:
                pass

    @is_aphost()
    @app_commands.default_permissions(manage_messages=True)
    @db.command()
    @app_commands.describe(url="URL to an Archipelago datapackage",
                           import_classifications="Import community classifications from a third-party repository?")
    async def import_datapackage(self, interaction: discord.Interaction, url: str = "https://archipelago.gg/datapackage", import_classifications: bool = True):
        """Import items and locations from an Archipelago datapackage into the database."""

        with sqlcon.cursor() as cursor:

            deferpost = await interaction.response.defer(ephemeral=True, thinking=True,)
            newpost = await interaction.original_response()

            data = requests.get(url, timeout=5)
            datapackage = data.json()

            games = list(datapackage['games'].keys())
            if "Archipelago" in games:
                del datapackage['games']["Archipelago"] # Skip the Archipelago data
                games.remove("Archipelago")

            msg = f"The datapackage provided has data for:\n\n{", ".join(games)}\n\nImport in progress..."
            if len(msg) > 2000:
                msg = f"The datapackage provided has data for {len(games)} games. Import in progress..."
            await newpost.edit(content=msg)

            for game, data in datapackage['games'].items():
                games_list = list(datapackage['games'].keys())
                if "Archipelago" in games_list: games_list.remove("Archipelago")
                current_index = games_list.index(game)
                next_game = games_list[current_index + 1] if current_index + 1 < len(games_list) else None

                # Retrieve community progression data if it exists
                comm_classification_table = {}
                classification = None
                if import_classifications:
                    community_progression = requests.get(f"https://raw.githubusercontent.com/silasary/world_data/refs/heads/main/worlds/{game}/progression.txt")
                    if community_progression.status_code == 200:
                        for line in community_progression.text.splitlines():
                            # Each line is in the format 'Item Name: classification'
                            # Interpret everything up to the final ':' as the item name
                            if ':' in line:
                                comm_classification_table[line.rsplit(':', 1)[0].strip()] = line.rsplit(':', 1)[1].strip().lower()


                if game == "Archipelago": continue
                for item in data['item_name_groups']['Everything']:
                    logger.info(f"Importing {game}: {item} to item_classification")
                    if item in comm_classification_table.keys():
                        classification = comm_classification_table[item]
                        if classification in ["mcguffin", "progression", "useful", "currency", "filler", "trap"]:
                            pass
                        else:
                            classification = None
                    cursor.execute(
                        "INSERT INTO archipelago.item_classifications (game, item, classification) VALUES (%s, %s, %s) ON CONFLICT (game, item) DO UPDATE SET classification = COALESCE(EXCLUDED.classification, archipelago.item_classifications.classification);",
                        (game, item, classification))
                for location in data['location_name_groups']['Everywhere']:
                    logger.info(f"Importing {game}: {location} to game_locations")
                    # Any location that shows up in the datapackage appears to be checkable
                    cursor.execute(
                        "INSERT INTO archipelago.game_locations (game, location, is_checkable) VALUES (%s, %s, %s) ON CONFLICT (game, location) DO UPDATE SET is_checkable = EXCLUDED.is_checkable;",
                        (game, location, True))
                # Find the next game to import, if any

                if next_game:
                    await newpost.edit(content=f"Imported {game}, working on {next_game}...")
                else:
                    pass
                    # await newpost.edit(content=f"Imported {game}, finishing up...")

        return await newpost.edit(content="Import *should* be complete!")

    aproom = app_commands.Group(name="room",description="Commands to do with the current room")

    async def link_slot_unlinked_complete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        """Complete the slot name for linking, only showing unlinked slots."""
        players = []
        with sqlcon.cursor() as cursor:
            cursor.execute("""
                SELECT player_name 
                FROM pepper.ap_room_players 
                WHERE guild = %s 
                AND player_name IN (
                    SELECT player_name FROM pepper.ap_players WHERE discord_user IS NULL
                )
            """, (ctx.guild_id,))
            for row in cursor.fetchall():
                players.append(row[0])

        # permitted_values = self.ctx.extras['ap_rooms'][ctx.guild_id]['players']
        if len(current) == 0:
            return [app_commands.Choice(name=opt,value=opt) for opt in players]
        else:
            return [app_commands.Choice(name=opt,value=opt) for opt in players if current.lower() in opt.lower()]

    async def link_slot_complete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        if not self.ctx.extras.get('ap_rooms'):
            self.fetch_guild_room(ctx.guild_id)
        permitted_values = self.ctx.extras['ap_rooms'][ctx.guild_id]['players']
        if len(current) == 0:
            return [app_commands.Choice(name=opt,value=opt) for opt in permitted_values]
        else:
            return [app_commands.Choice(name=opt,value=opt) for opt in permitted_values if current.lower() in opt.lower()]

    @aproom.command()
    @app_commands.autocomplete(slot_name=link_slot_unlinked_complete)
    async def link_slot(self, interaction: discord.Interaction, slot_name: str, user: discord.User = None):
        """Link an Archipelago slot name to your Discord account."""

        if user is None:
            user = interaction.user

        cmd = "UPDATE pepper.ap_players SET discord_user = %s WHERE player_name = %s"
        with sqlcon.cursor() as cursor:
            cursor.execute(cmd, (user.id, slot_name))
            # sqlcon.commit()

        logger.info(f"Linked {slot_name} to {user.display_name} ({user.id}) in {interaction.guild.name} ({interaction.guild.id})")
        return await interaction.response.send_message(f"Linked {slot_name} to {user.display_name} ({user.id})!",ephemeral=True)

    @aproom.command()
    @is_aphost()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.guild_only()
    @app_commands.describe(room_url="Link to the Archipelago room")
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
                    # This is a PostgreSQL function that deals with updating
                    # all the various tables that need to be updated
                    # Master room table: pepper.ap_all_rooms
                    # Master players table: pepper.ap_players
                    # Active rooms/players table: pepper.ap_room_players
                    '''SELECT pepper.create_aproom(%s, %s, %s, %s, %s);''',
                    (room_id, interaction.guild_id, players, hostname, room_port)
                ),
            ]
            # When we're ready
            for command in commands:
                logger.info(f"Executing SQL: {command[0]} with {command[1]}")
                cmd, params = command
                try:
                    cursor.execute(cmd, params)
                except psql.Error as e:
                    logger.error(f"Error executing SQL command: {e}")
                    await newpost.edit(content=f"**Error**: there was a problem executing the SQL command. Please try again later.\n\n```{e}```")
                    return

        logger.info("SQL commands executed.")
        logger.info("Setting up room data...")
        self.fetch_guild_room(interaction.guild_id)

        logger.info(f"Set room for {interaction.guild.name} ({interaction.guild.id}) to {room_url}")
        await newpost.edit(content=f"Set room for {interaction.guild.name} to {room_url} !")

    @aproom.command(name="status")
    async def room_status(self, interaction: discord.Interaction, public: bool = False):
        """Get the status of the current Archipelago room."""
        deferpost = await interaction.response.defer(ephemeral=not public, thinking=True,)
        newpost = await interaction.original_response()

        if not self.ctx.extras.get('ap_rooms'):
            self.ctx.extras['ap_rooms'] = {}
            self.fetch_guild_room(interaction.guild_id)
            if not self.ctx.extras['ap_rooms'].get(interaction.guild_id):
                return await newpost.edit(content="No Archipelago room is currently set for this server.")

        room = self.ctx.extras['ap_rooms'].get(interaction.guild_id)
        if not room:
            return await newpost.edit(content="No Archipelago room is currently set for this server.")

        game_table = requests.get(f"http://localhost:42069/inspectgame", timeout=10).json()

        if not game_table:
            return await newpost.edit(content="Couldn't fetch the game table from the running Archipelago game.")
        
        msg_lines = []

        msg_lines.append(f"## Archipelago Room Status")

        with sqlcon.cursor() as cursor:
            try:
                cursor.execute("SELECT room_id, host, port from pepper.ap_all_rooms WHERE active = 'true' AND guild = %s;", (interaction.guild_id,))
                room_id, host, port = cursor.fetchone()
                msg_lines.append(f"**Room ID** [{room_id}](https://{host}/room/{room_id}) (`{host}:{port}`)")
            except psql.Error as e:
                pass

        msg_lines.append(f"This game is {round(game_table['collection_percentage'],2)}% complete. ({game_table['collected_locations']} out of {game_table['total_locations']} locations checked.)")
        
        msg_lines.append("")

        for player in game_table['players'].values():
            if player['goaled'] is True:
                msg_lines.append(f"**{player['name']} ({player['game']})**: finished their game.")
            else:
                msg_lines.append(f"**{player['name']} ({player['game']})**: {round(player['collection_percentage'], 2)}% complete. ({player['collected_locations']}/{player['total_locations']} checks.)")

        await newpost.edit(content="\n".join(msg_lines))

    @aproom.command()
    async def get_hints(self, interaction: discord.Interaction, public: bool = False):
        """Get hints for the current room."""

        deferpost = await interaction.response.defer(ephemeral=not public, thinking=True,)
        newpost = await interaction.original_response()


        if not self.ctx.extras.get('ap_rooms'):
            self.ctx.extras['ap_rooms'] = {}
            self.fetch_guild_room(interaction.guild_id)
            if not self.ctx.extras['ap_rooms'].get(interaction.guild_id):
                return await newpost.edit(content="No Archipelago room is currently set for this server.")

        room = self.ctx.extras['ap_rooms'].get(interaction.guild_id)
        if not room:
            return await newpost.edit(content="No Archipelago room is currently set for this server.")

        room_slots = requests.get(f"https://{room['host']}/api/room_status/{room['room_id']}", timeout=10).json()['players']

        linked_slots = []
        with sqlcon.cursor() as cursor:
            cursor.execute(
                "SELECT rp.player_name FROM pepper.ap_room_players rp JOIN pepper.ap_players p ON rp.player_name = p.player_name WHERE rp.room_id = %s AND rp.guild = %s AND p.discord_user = %s;",
                (room["room_id"], interaction.guild_id, interaction.user.id),
            )
            linked_slots = [row[0] for row in cursor.fetchall()]
        if len(linked_slots) == 0:
            return await newpost.edit(content="None of your Archipelago slots are linked to this game.")

        # Get the game table
        game_table = requests.get(f"http://localhost:42069/inspectgame", timeout=10).json()

        # Build the hint table
        hint_table = {}
        for slot in linked_slots:
            if slot in game_table['players']:
                hint_table[slot] = {
                    h['location']: {"item": h['name'],
                                 "sender": h['sender'],
                                 "receiver": h['receiver'],
                                 "classification": h['classification'],
                                 "entrance": h['location_entrance'],
                                 "costs": h['location_costs'],
                                } for h in game_table['players'][slot]['hints']['sending'] if h['classification'] not in ["trap", "filler"]}
                hint_table[slot].update({
                    h['location']: {"item": h['name'],
                                 "sender": h['sender'],
                                 "receiver": h['receiver'],
                                 "classification": h['classification'],
                                 "entrance": h['location_entrance'],
                                 "costs": h['location_costs'],
                                } for h in game_table['players'][slot]['hints']['receiving'] if h['classification'] not in ["trap", "filler"]})

        # Format the hint table
        hint_table_list = []
        for slot, hints in hint_table.items():
            for location, details in hints.items():
                hint_table_list.append({
                    "Slot": slot,
                    "Item": details["item"],
                    "Location": location,
                    "Entrance": details["entrance"],
                    "Costs": details["costs"],
                    "Sender": details["sender"],
                    "Receiver": details["receiver"],
                    "Classification": details["classification"],
                })

        if len(hint_table_list) == 0:
            return await newpost.edit(content="No hints available for your linked slots.")

        hints_list = "## To Find:"
        for hint in hint_table_list:
            if hint["Sender"] not in linked_slots: continue
            if game_table['players'][hint["Receiver"]]['goaled'] is True or game_table['players'][hint["Receiver"]]['released'] is True: continue

            if hint["Sender"] == hint["Receiver"]:
                hints_list += f"\n**Your {hint['Item']}** is on {hint['Location']}{f" at {hint['Entrance']}" if hint['Entrance'] else ""}."
                if bool(hint['Costs']):
                    hints_list += f"\n> -# This will cost {join_words(hint['Costs'])} to obtain."
            else:
                hints_list += f"\n**{hint['Receiver']}'s {hint['Item']}** is on {hint['Location']}{f" at {hint['Entrance']}" if hint['Entrance'] else ""}."
                if bool(hint['Costs']):
                    hints_list += f"\n> -# This will cost {join_words(hint['Costs'])} to obtain."

        hints_list += "\n\n## To Be Found:"
        for hint in hint_table_list:
            if hint["Receiver"] not in linked_slots: continue
            if hint["Sender"] == hint["Receiver"]: continue
            hints_list += f"\n**Your {hint['Item']}** is on {hint['Sender']}'s {hint['Location']}{f" at {hint['Entrance']}" if hint['Entrance'] else ""}."
            if bool(hint['Costs']):
                hints_list += f" (Costs {join_words(hint['Costs'])})"


        await newpost.edit(content=hints_list)

    # itemlogging = app_commands.Group(name="itemlog",description="Manage an item logging webhook")

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

    def fetch_guild_room(self, guild_id: int) -> dict:
        if self.ctx.extras['ap_rooms'].get(guild_id, {}):
            return self.ctx.extras['ap_rooms'].get(guild_id, {})
        else:
            with sqlcon.cursor() as cursor:
                cursor.execute("SELECT * FROM pepper.ap_all_rooms WHERE guild = %s and active = 'true' LIMIT 1", (guild_id,))
                result = cursor.fetchone()
                if result:
                    roomdict = {
                        'room_id': result[0],
                        'seed': result[1],
                        'guild_id': result[2],
                        'active': result[3],
                        'host': result[4],
                        'players': result[5],
                        'version': result[6],
                        'last_line': result[7],
                        'last_activity': result[8],
                        'port': result[9]
                    }
                    self.ctx.extras['ap_rooms'][guild_id] = roomdict
                    return roomdict
                else:
                    return {}

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.ctx.procs.get('archipelago'):
            self.ctx.procs['archipelago'] = {}

        if not self.ctx.extras.get('ap_rooms'):
            self.ctx.extras['ap_rooms'] = {}
            # Load persisted ap_rooms.json if it exists
            for guilds in self.ctx.guilds:
                if not self.ctx.extras['ap_rooms'].get(guilds.id):
                    self.ctx.extras['ap_rooms'][guilds.id] = {}
                    self.fetch_guild_room(guilds.id)

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
                    self.ctx.procs['archipelago'][log['guild']] = process
                except:
                    logger.error("Error starting log:",exc_info=True)

async def setup(bot):
    logger.info("Loading Archipelago cog extension.")
    await bot.add_cog(Archipelago(bot))

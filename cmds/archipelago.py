from datetime import datetime, timedelta, timezone
import json
import os
import sys
import subprocess
import requests
import logging
import signal

import urllib3.exceptions
import yaml
import traceback
import typing
from collections import defaultdict
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
from collections import defaultdict
import time

cfg = None
MAX_MSG_LENGTH = 2000

logger = logging.getLogger('discord.ap')

with open('config.yaml', 'r', encoding='UTF-8') as file:
    cfg = yaml.safe_load(file)

sqlcfg = cfg['bot']['psql']
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

def is_classifier():
    async def predicate(ctx):
        return ctx.user.get_role(1450512048583610502) is not None
    return commands.check(predicate)

class Archipelago(commands.GroupCog, group_name="archipelago"):
    """Commands relating to the Archipelago randomizer"""

    def __init__(self, bot):
        self.ctx = bot

    messages = {
        "no_slots_linked": 
            """None of your linked Archipelago slots are linked to this game.
            **Maybe you haven't linked a slot to your Discord account yet?**
            Use `/archipelago room link_slot` to link any of your slots in this game,
            and then try using this command again.""",
    }

    async def cog_command_error(self, ctx: Context[BotT], error: Exception) -> None:
        await ctx.reply(f"Command error: {error}",ephemeral=True)

    async def archivist_log(self, interaction: discord.Interaction, type: str, message: str):
        """Log an action to the archivist log channel, if set."""
        
        if interaction.guild.id != 1424283904260706378:
            logger.error("Attempted to log to archivist channel outside of main server.")
            return False
        
        channel_id = self.ctx.procs['archipelago'].get('channel_archivist')
        if not channel_id:
            logger.info("No archivist channel set; skipping log.")
            return False

        channel = self.ctx.get_channel(channel_id)
        if not channel:
            logger.error("Archivist channel ID is invalid; cannot log.")
            return False
        
        match type:
            case "classify":
                type = "Item Classification"
            case "describe":
                type = "Item Description"
            case _:
                pass
        
        try:
            embed = discord.Embed(
                title=f"Archival Log: {type.title()}", 
                description=message, 
                timestamp=datetime.now(timezone.utc))
            embed.set_footer(text=f"Action by {interaction.user.display_name}", icon_url=interaction.user.display_avatar.url)
            await channel.send(embed=embed)
            logger.info(f"Logged action to archivist channel: {type}")
        except AttributeError as err:
            raise AttributeError("Action successful, but couldn't log to channel: " + str(err))

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

    @is_classifier()
    @db.command(name='classify_item')
    @app_commands.describe(game="The game that contains the item",
                           item="The item to act on (wildcards: ? one, % many)",
                           classification="The item's importance")
    @app_commands.autocomplete(game=db_game_complete,item=db_item_complete,classification=db_classification_complete)
    async def db_update_item_classification(self, interaction: discord.Interaction, game: str, item: str, classification: str):
        """Update the classification of an item."""
        # Defer the response because contacting itemlogs may take time
        await interaction.response.defer(ephemeral=True, thinking=True)
        cursor = sqlcon.cursor()

        if '%' in item or r'?' in item:
            cursor.execute("UPDATE archipelago.item_classifications SET classification = %s where game = %s and item like %s RETURNING item", (classification.lower(), game, item))
            sqlcon.commit()
            count = cursor.rowcount
            matched_items = [r[0] for r in cursor.fetchall()]
            logger.info(f"Classified {str(count)} item(s) matching '{item}' in {game} to {classification}")

            await self.archivist_log(interaction, "classify", f"Classified **{join_words(matched_items)}** in **{game}** to **{classification.title()}**.")

            # Send immediate acknowledgement so user knows we're working on notifying itemlogs
            ack_msg = await interaction.followup.send(
                f"Classification updated for {game} (matched {count} items). Contacting running itemlogs to refresh classifications...",
                ephemeral=True,
            )

            # Notify running itemlogs to refresh this game's classifications (wildcard -> refresh whole game)
            refreshed = 0
            attempted = 0
            try:
                cursor.execute("SELECT flask_port FROM pepper.ap_all_rooms WHERE active = 'true' AND flask_port IS NOT NULL;")
                ports = [r[0] for r in cursor.fetchall()]
            except Exception:
                ports = []

            for port in ports:
                attempted += 1
                try:
                    resp = requests.get(f"http://localhost:{port}/refreshclassifications", params={'game': game}, timeout=3)
                    if resp.status_code == 200:
                        refreshed += 1
                except requests.RequestException:
                    logger.warning(f"Failed to contact itemlog at port {port} to refresh classifications for game {game}.")

            final_reply = f"Classification for {game}'s {str(count)} items matching '{item}' was successful."
            if attempted > 0:
                final_reply += f" Notified {refreshed}/{attempted} running itemlog(s) to refresh classifications for the game '{game}'."
            else:
                final_reply += " No running itemlogs were found to notify."

            try:
                await ack_msg.edit(content=final_reply)
            except Exception:
                # If editing fails, send a followup instead
                await interaction.followup.send(final_reply, ephemeral=True)
            return
        else:
            try:
                cursor.execute("UPDATE archipelago.item_classifications SET classification = %s where game = %s and item = %s", (classification.lower(), game, item))
                sqlcon.commit()
                logger.info(f"Classified '{item}' in {game} to {classification}")

                # Send immediate acknowledgement so user knows we're working on notifying itemlogs
                ack_msg = await interaction.followup.send(
                    f"Classification updated for {game}: '{item}'. Contacting running itemlogs to refresh classifications...",
                    ephemeral=True,
                )

                # Notify running itemlog instances to refresh their in-memory classifications for this specific item
                refreshed = 0
                attempted = 0
                try:
                    cursor.execute("SELECT flask_port FROM pepper.ap_all_rooms WHERE active = 'true' AND flask_port IS NOT NULL;")
                    ports = [r[0] for r in cursor.fetchall()]
                except Exception:
                    ports = []

                for port in ports:
                    attempted += 1
                    try:
                        resp = requests.get(f"http://localhost:{port}/refreshclassifications", params={'game': game, 'item': item}, timeout=3)
                        if resp.status_code == 200:
                            refreshed += 1
                    except requests.RequestException:
                        logger.warning(f"Failed to contact itemlog at port {port} to refresh classifications for {game}:{item}.")

                await self.archivist_log(interaction, "classify", f"Classified **{item}** in **{game}** to **{classification.title()}**.")

                # Inform the user of success and how many itemlogs were notified
                final_reply = f"Classification for {game}'s '{item}' was successful."
                if attempted > 0:
                    final_reply += f" Notified {refreshed}/{attempted} running itemlog(s) to refresh classifications for '{game}:{item}'."
                else:
                    final_reply += " No running itemlogs were found to notify."

                try:
                    await ack_msg.edit(content=final_reply)
                except Exception:
                    await interaction.followup.send(final_reply, ephemeral=True)
                return
            finally:
                pass

    @is_classifier()
    @db.command(name='describe_item')
    @app_commands.describe(game="The game that contains the item",
                           item="The item to act on")
    @app_commands.autocomplete(game=db_game_complete,item=db_item_complete)
    async def db_set_item_description(self, interaction: discord.Interaction, game: str, item: str):
        """Set the description of an item using a Discord popup window."""
        cursor = sqlcon.cursor()

        existing_description = None

        # Check if a description already exists
        cursor.execute("SELECT description FROM archipelago.item_classifications WHERE game = %s AND item = %s", (game, item))
        result = cursor.fetchone()
        if result and result[0]:
            # If a description already exists, we'll put it as the modal placeholder
            existing_description = result[0]

        class DescriptionForm(discord.ui.Modal):
            """A Discord modal for setting an item's description."""
            cogself = None
            def __init__(self, cogself, game: str, item: str):
                super().__init__(title=f"{item} ({game})"[:45])  # Title must be under 45 characters
                self.cogself = cogself
                self.game = game
                self.item = item

            description = discord.ui.TextInput(label="Description",
                            style=discord.TextStyle.paragraph,
                            placeholder=existing_description[:96] + "..." if bool(existing_description) else f"Enter the description for {item} here.",
                            required=True,
                            max_length=500)

            async def on_submit(self, interaction: discord.Interaction):
                description = self.description.value
                cursor.execute("UPDATE archipelago.item_classifications SET description = %s WHERE game = %s AND item = %s",
                               (description, self.game, self.item))
                await interaction.response.send_message(f"Description for {self.game}'s '{self.item}' has been set.", ephemeral=True)
                await self.cogself.archivist_log(interaction, "describe", f"Set description for **{self.item}** in **{self.game}**.")
                logger.info(f"User {interaction.user.display_name} ({interaction.user.id}) set description for {self.game}'s {self.item}.")

        # Create the modal and send it to the user
        return await interaction.response.send_modal(DescriptionForm(self, game, item))

    # Uncomment this command when the itemlog is running off Pepper too
    # So we can crossreference the itemlog message with the mentioned items/etc

    # @app_commands.context_menu(name="AP: Explain Item")
    # async def explain_item(self, interaction: discord.Interaction, msg: discord.Message):
    #     """Explain an item in the current Archipelago room."""
    #     if not self.ctx.extras.get('ap_rooms'):
    #         self.ctx.extras['ap_rooms'] = {}
    #         self.fetch_guild_room(interaction.guild_id)
    #         if not self.ctx.extras['ap_rooms'].get(interaction.guild_id):
    #             return await interaction.response.send_message("No Archipelago room is currently set for this server.",ephemeral=True)

    #     room = self.ctx.extras['ap_rooms'].get(interaction.guild_id)
    #     if not room:
    #         return await interaction.response.send_message("No Archipelago room is currently set for this server.",ephemeral=True)

    #     game = room['game']
    #     item_name = item.content.strip()

    #     with sqlcon.cursor() as cursor:
    #         cursor.execute("SELECT classification, description FROM archipelago.item_classifications WHERE game = %s AND item = %s", (game, item_name))
    #         result = cursor.fetchone()

    #     if result:
    #         embed = discord.Embed(title=f"{item_name} ({game})")
    #         embed.add_field(name="Classification", value=result[0].title(), inline=False)
    #         embed.add_field(name="Description", value=result[1] if result[1] else "No description available.", inline=False)
    #     else:
    #         msg = f"No classification found for **{item_name}** in **{game}**."

        # return await interaction.response.send_message(msg, ephemeral=True)

    @is_aphost()
    @app_commands.default_permissions(manage_messages=True)
    @db.command()
    @app_commands.describe(url="URL to an Archipelago datapackage")
    async def import_datapackage(self, interaction: discord.Interaction, url: str = "https://archipelago.gg/datapackage", export_json: discord.Attachment = None):
        """Import items and locations from an Archipelago datapackage into the database."""

        with sqlcon.cursor() as cursor:

            deferpost = await interaction.response.defer(ephemeral=True, thinking=True,)
            newpost = await interaction.original_response()

            if export_json:
                try: # Make sure it's actually json
                    if export_json.content_type != 'application/json; charset=utf-8':
                        logger.error(f"Import datapackage: provided file has invalid content type {export_json.content_type}")
                        return await newpost.edit(content="**Error**: the provided file is not valid JSON.",delete_after=15.0)
                    data = await export_json.read()
                    datapackage = json.loads(data)
                except Exception as e:
                    return await newpost.edit(content=f"**Error**: {e}",delete_after=15.0)
            else:
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
                checksum = data.get('checksum', None)


                if game == "Archipelago": continue

                # Check if this checksum already exists in the database
                cursor.execute("SELECT 1 FROM archipelago.item_classifications WHERE game = %s AND datapackage_checksum = %s LIMIT 1;", (game, checksum))
                if cursor.fetchone():
                    logger.info(f"Datapackage for {game} with checksum {checksum} is already imported; skipping.")
                    if next_game:
                        await newpost.edit(content=f"Skipped {game} (already imported), working on {next_game}...")
                    else:
                        pass
                    continue

                for item in data['item_name_groups']['Everything']:
                    logger.info(f"Importing {game}: {item} to item_classification")
                    cursor.execute(
                        "INSERT INTO archipelago.item_classifications (game, item, classification, datapackage_checksum) VALUES (%s, %s, %s, %s) ON CONFLICT (game, item) DO UPDATE SET classification = COALESCE(EXCLUDED.classification, archipelago.item_classifications.classification), datapackage_checksum = COALESCE(EXCLUDED.datapackage_checksum, archipelago.item_classifications.datapackage_checksum);",
                        (game, item, classification, checksum))
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

        return await newpost.edit(content="Import *should* be complete!")
    
    @is_aphost()
    @db.command()
    @app_commands.default_permissions(manage_messages=True)
    async def cleanup_fake_items(self, interaction: discord.Interaction):
        """Remove items from the database that don't have a datapackage checksum (likely fake/event items from spoiler logs)."""
        with sqlcon.cursor() as cursor:
            deferpost = await interaction.response.defer(ephemeral=True, thinking=True,)
            newpost = await interaction.original_response()

            # Delete items without checksum
            cursor.execute("DELETE FROM archipelago.item_classifications WHERE datapackage_checksum IS NULL OR datapackage_checksum = ''")
            deleted_count = cursor.rowcount
            sqlcon.commit()

            await newpost.edit(content=f"Cleaned up {deleted_count} fake items from the database.")
    
    @is_aphost()
    @db.command()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.autocomplete(game=db_game_complete)
    @app_commands.describe(game="The game to import classifications for (omit to import all)", skip_classified="Skip items that already have a classification")
    async def import_classifications(self, interaction: discord.Interaction, game: str = None, skip_classified: bool = True):
        """Import community classifications from a third-party repository."""
        deferpost = await interaction.response.defer(ephemeral=True, thinking=True,)
        newpost = await interaction.original_response()

        comm_classification_table = {}

        def fetch_classifications(game: str):
            nonlocal comm_classification_table
            community_progression = requests.get(f"https://raw.githubusercontent.com/silasary/world_data/refs/heads/main/worlds/{game}/progression.txt")
            if community_progression.status_code == 200:
                comm_classification_table[game] = {}
                for line in community_progression.text.splitlines():
                    # Each line is in the format 'Item Name: classification'
                    # Interpret everything up to the final ':' as the item name
                    if ':' in line:
                        comm_classification_table[game][line.rsplit(':', 1)[0].strip()] = line.rsplit(':', 1)[1].strip().lower()
                logger.info(f"Retrieved community classifications for {game} from world_data repository.")

        # Get a list of games in our database
        if not bool(game):
            with sqlcon.cursor() as cursor:
                    cursor.execute("SELECT DISTINCT game FROM archipelago.item_classifications;")
                    db_games = [row[0] for row in cursor.fetchall()]

            for game in db_games:
                fetch_classifications(game)
        else:
            fetch_classifications(game)

        
        # Update the item_classifications table with the community classifications
        skipped = 0
        processed = 0

        with sqlcon.cursor() as cursor:
            for game, classifications in comm_classification_table.items():
                for item, classification in classifications.items():
                    if classification not in ["mcguffin", "progression", "conditional progression", "useful", "currency", "filler", "trap"]:
                        logger.warning(f"Invalid classification '{classification}' for {game}: {item}. Skipping.")
                        skipped += 1
                        continue
                    if classification == "mcguffin":
                        classification = "progression"
                    if skip_classified:
                        cursor.execute(
                            "UPDATE archipelago.item_classifications SET classification = %s where game = %s and item = %s and classification is null;",
                            (classification, game, item))
                    else:
                        cursor.execute(
                            "UPDATE archipelago.item_classifications SET classification = %s where game = %s and item = %s;",
                            (classification, game, item))
                    processed += 1
                    logger.info(f"Updated {game}: {item} to {classification} in item_classifications table.")

        return await newpost.edit(content=f"Import of community classifications complete! Processed {processed} items, skipped {skipped} items (bad classifications).")
    
    @is_aphost()
    @db.command()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.autocomplete(game=db_game_complete)
    async def export_classifications(self, interaction: discord.Interaction, game: str):
        """Export classifications from the database to a file compatible with the community repository."""

        # deferpost = await interaction.response.defer(ephemeral=True, thinking=True,)
        # newpost = await interaction.original_response()

        export_data = defaultdict(str)

        with sqlcon.cursor() as cursor:
            cursor.execute("SELECT item, classification FROM archipelago.item_classifications WHERE game = %s and classification IS NOT NULL ORDER BY item asc;", (game,))
            for item, classification in cursor.fetchall():
                export_data[item] = classification

        response = "\n".join([f"{item}: {classification}" for item, classification in export_data.items()])

        responsefile = bytes(response,encoding='UTF-8')
        return await interaction.response.send_message("Here's the result, as a file:",file=discord.File(BytesIO(responsefile), 'result.txt'),ephemeral=True)

    @db_update_item_classification.error
    @db_set_item_description.error
    async def db_permissions_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.MissingRole):
            return await interaction.response.send_message("You need the `archivist` role in order to manipulate the bot's Archipelago database.",ephemeral=True)
        else:
            return await interaction.response.send_message(f"Database command error: {error}",ephemeral=True)

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

    async def user_linked_slots_complete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        """Complete the slot name for linking, only showing slots linked to the requesting player."""
        players = []
        with sqlcon.cursor() as cursor:
            cursor.execute("""
                SELECT player_name 
                FROM pepper.ap_room_players 
                WHERE guild = %s 
                AND player_name IN (
                    SELECT player_name FROM pepper.ap_players WHERE discord_user = %s
                )
            """, (ctx.guild_id,ctx.user.id))
            for row in cursor.fetchall():
                players.append(row[0])

        # permitted_values = self.ctx.extras['ap_rooms'][ctx.guild_id]['players']
        if len(current) == 0:
            return [app_commands.Choice(name=opt,value=opt) for opt in players]
        else:
            return [app_commands.Choice(name=opt,value=opt) for opt in players if current.lower() in opt.lower()]

    @aproom.command()
    @app_commands.autocomplete(slot_name=link_slot_unlinked_complete)
    async def link_slot(self, interaction: discord.Interaction, slot_name: str):
        """Link an Archipelago slot name to your Discord account."""

        user = interaction.user

        cmd = "UPDATE pepper.ap_players SET discord_user = %s WHERE player_name = %s"
        with sqlcon.cursor() as cursor:
            cursor.execute(cmd, (user.id, slot_name))
            # sqlcon.commit()

        logger.info(f"Linked {slot_name} to {user.display_name} ({user.id}) in {interaction.guild.name} ({interaction.guild.id})")
        return await interaction.response.send_message(f"Linked **{slot_name}** to **{user.display_name}**!",ephemeral=True)

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
    @app_commands.describe(
        public="Publish the status to the room, instead of just to you",
        filter_self="Show only your own slots",
        filter_active="Show only players that have not finished or released",
        show_slot_game = "Show the game that each slot is playing",
        show_goals = "Show each player's goal in the status"
    )
    async def room_status(self, interaction: discord.Interaction, public: bool = False, filter_self: bool = False, filter_active: bool = False, show_slot_game: bool = True, show_goals: bool = True):
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
        api_port = room['flask_port']
        if not api_port:
            self.fetch_guild_room(interaction.guild_id)
            api_port = room['flask_port']
            if not api_port: return await newpost.edit(content="Something is wrong, and the API port found for this room is invalid. Contact Splatsune.")

        try:
            game_table = requests.get(f"http://localhost:{api_port}/inspectgame", timeout=10).json()
        except ConnectionError:
            return await newpost.edit(
                content="Couldn't connect to the running Archipelago game. It might be restarting.\nTry again in a minute or two.")
        
        def player_status(player: dict) -> list[str]:
            status_lines = []
            last_online = lambda player: "Online right now." if player['online'] is True else f"Last online <t:{int(player['last_online'])}:R>." if player['last_online'] is not None else "Never logged in."
            showgame_ifenabled = lambda player: f" ({player['game']})" if show_slot_game else ''
            if player['goaled'] is True:
                status_lines.append(f"- **{player['name']}{showgame_ifenabled(player)}**: finished their game with {round(player['finished_percentage'], 2)}% checks collected.")
            elif player['released'] is True and player['goaled'] is False:
                status_lines.append(f"- **{player['name']}{showgame_ifenabled(player)}**: released from the game.")
            else:
                status_lines.append(f"- **{player['name']}{showgame_ifenabled(player)}**: {round(player['collection_percentage'], 1)}% complete. ({player['collected_locations']}/{player['total_locations']} checks.) {last_online(player)}")
            if player['stats']['goal_str'] is not None and show_goals:
                status_lines.append(f"  - Goal: {player['stats']['goal_str']}.")
            return status_lines
        
        msg_lines = []

        msg_lines.append(f"## Archipelago Room Status")

        with sqlcon.cursor() as cursor:
            try:
                cursor.execute("SELECT room_id, host, port from pepper.ap_all_rooms WHERE active = 'true' AND guild = %s;", (interaction.guild_id,))
                room_id, host, port = cursor.fetchone()
                msg_lines.append(f"**Room ID** [{room_id}](<https://{host}/room/{room_id}>) (`{host}:{port}`)")
            except psql.Error as e:
                pass

        msg_lines.append(f"This game is {round(game_table['collection_percentage'],2)}% complete. ({game_table['collected_locations']} out of {game_table['total_locations']} locations checked.)")
        if game_table['running'] is False:
            msg_lines.append("The game is currently spun down - visit the room page to bring it back up.")

        msg_lines.append("")

        linked_slots = []
        with sqlcon.cursor() as cursor:
            cursor.execute(
                "SELECT rp.player_name FROM pepper.ap_room_players rp JOIN pepper.ap_players p ON rp.player_name = p.player_name WHERE rp.room_id = %s AND rp.guild = %s AND p.discord_user = %s;",
                (room["room_id"], interaction.guild_id, interaction.user.id),
            )
            linked_slots = [row[0] for row in cursor.fetchall()]
        if len(linked_slots) == 0:
            return await newpost.edit(content=self.messages['no_slots_linked'])

        linked_player_list  = {k: v for k,v in game_table['players'].items() if v['name'] in linked_slots}
        other_players_list = {k: v for k,v in game_table['players'].items() if v['name'] not in linked_slots}
        if filter_active:
            other_players_list = {k: v for k, v in other_players_list.items() if not v['goaled'] and not v['released']}

        msg_lines.append("## Your Slots:")
        for slot_name, data in linked_player_list.items():
            msg_lines.extend(player_status(data))

        if not filter_self:
            msg_lines.append("")

            sorted_other_players_list = iter(sorted(other_players_list.items(), key=lambda p: (not p[1]['online'], -int(p[1].get('last_online') or 0) )))

            msg_lines.append(f"## {len(other_players_list)} Other Players:")
            while (len("\n".join(msg_lines)) < 1900):

                    try:
                        player = next(sorted_other_players_list)[1]
                        new_lines = player_status(player)
                        
                        if len("\n".join(msg_lines)) + len("\n".join(new_lines)) > 1800: 
                            msg_lines.append("- ...and more players not shown to avoid message length limits.")
                            break
                        else:
                            msg_lines.extend(new_lines)
                    except StopIteration:
                        break


        return await newpost.edit(content="\n".join(msg_lines))

    @aproom.command(name="received")
    @app_commands.choices(minimum_importance=[
        app_commands.Choice(name="Useful", value="useful"),
        app_commands.Choice(name="Progression", value="progression")
    ])
    @app_commands.describe(minimum_importance="Minimum importance of items to show",
                           include_unclassified="Include unclassified items")
    async def received_items(self, interaction: discord.Interaction, minimum_importance: str = "useful", include_unclassified: bool = True):
        """Get a list of items you received since last played."""

        show_classifications = ["useful", "progression"]
        if minimum_importance == "progression":
            show_classifications = ["progression"]
        if include_unclassified:
            show_classifications.append(None)

        deferpost = await interaction.response.defer(ephemeral=True, thinking=True,)
        newpost = await interaction.original_response()

        if not self.ctx.extras.get('ap_rooms'):
            self.ctx.extras['ap_rooms'] = {}
            self.fetch_guild_room(interaction.guild_id)
            if not self.ctx.extras['ap_rooms'].get(interaction.guild_id):
                return await newpost.edit(content="No Archipelago room is currently set for this server.")

        room = self.ctx.extras['ap_rooms'].get(interaction.guild_id)
        api_port = room['flask_port']
        if not room:
            return await newpost.edit(content="No Archipelago room is currently set for this server.")

        try:
            game_table = requests.get(f"http://localhost:{api_port}/inspectgame", timeout=10).json()
        except ConnectionError|urllib3.exceptions.MaxRetryError|requests.exceptions.ConnectionError:
            return await newpost.edit(
                content="Couldn't connect to the running Archipelago game. It might be restarting.\nTry again in a minute or two.")

        linked_slots = []
        with sqlcon.cursor() as cursor:
            cursor.execute(
                "SELECT rp.player_name FROM pepper.ap_room_players rp JOIN pepper.ap_players p ON rp.player_name = p.player_name WHERE rp.room_id = %s AND rp.guild = %s AND p.discord_user = %s;",
                (room["room_id"], interaction.guild_id, interaction.user.id),
            )
            linked_slots = [row[0] for row in cursor.fetchall()]
        if len(linked_slots) == 0:
            return await newpost.edit(content=self.messages['no_slots_linked'])
        
        player_table = {}

        for slot in linked_slots:
            player = game_table['players'][slot]

            # Check when the player was last online
            # If they were never online, set a default timestamp of 0
            # So they see all of the items they received
            player_last_online = player['last_online'] if player['last_online'] is not None else 0
            is_player_goaled = player['goaled']
            is_player_released = player['released']

            player_table[slot] = {
                "name": player['name'],
                "game": player['game'],
                "last_online": player_last_online,
                "online": player['online'],
                "goaled": is_player_goaled,
                "released": is_player_released,
                "offline_items": [],
            }

            for item in player['inventory']:
                try:
                    if item.get('received_timestamp', 0) > player_last_online and item['classification'] in show_classifications:
                        player_table[slot]['offline_items'].append({
                            "Item": item['name'],
                            "Sender": item['location']['player'],
                            "Receiver": item['receiver'],
                            "Classification": item['classification'],
                            "Location": item['location'],
                            "Timestamp": item['received_timestamp'],
                        })
                except TypeError:
                    # received_timestamp or player_last_online is probably None
                    # if the player is online right now and player_last_online is None, then add the item
                    if player_last_online is None and player_table[slot]['online'] is True:
                        if item['classification'] in show_classifications:
                            player_table[slot]['offline_items'].append({
                                "Item": item['name'],
                                "Sender": item['location']['player'],
                                "Receiver": item['receiver'],
                                "Classification": item['classification'],
                                "Location": item['location'],
                                "Timestamp": int(item['received_timestamp']),
                            })
                    elif item['received_timestamp'] is None and player_last_online is None:
                        # This is probably a starting item
                        item['received_timestamp'] = game_table['start_timestamp']
                        if item['classification'] not in ["trap", "filler", "currency"]:
                            player_table[slot]['offline_items'].append({
                                "Item": item['name'],
                                "Sender": item['location']['player'],
                                "Receiver": item['receiver'],
                                "Classification": item['classification'],
                                "Location": item['location'],
                                "Timestamp": int(item['received_timestamp']),
                            })
                    else:
                        # we tried; log the error and skip the item
                        logger.warning(f"Received item for {slot} has invalid timestamp data: {item['name']} - {item.get('received_timestamp', 'None')} vs {player_last_online}")
                        continue

        if all(len(player_table[slot]['offline_items']) == 0 for slot in linked_slots):
            return await newpost.edit(content="You have not received any items since you last played.")

        rcv_lines = ["## Received Items"]
        # Group items by slot and sort by timestamp
        try:
            # reverse sort each list (newest items first)
            for slot in linked_slots:
                raw_list = player_table[slot]['offline_items']
                player_table[slot]['offline_items'] = sorted(raw_list, key=lambda i: i['Timestamp'], reverse=True)

            item_lines = {}
            for slot in linked_slots:
                item_lines[slot] = []
                grouped = defaultdict(list)
                for item in player_table[slot]['offline_items']:
                    grouped[item['Item']].append(item)
                for item_name, items in grouped.items():
                    if len(items) <= 3:
                        for item in items:
                            line = f"- <t:{int(item['Timestamp'])}:R>: **{item['Item']}** from {item['Sender']}"
                            item_lines[slot].append(line)
                    else:
                        senders = sorted(set(item['Sender'] for item in items))
                        if len(senders) == 1:
                            sender_str = senders[0]
                        elif len(senders) == 2:
                            sender_str = f"{senders[0]} and {senders[1]}"
                        else:
                            sender_str = ', '.join(senders[:-1]) + f", and {senders[-1]}"
                        line = f"- **{item_name} (x{len(items)})** from {sender_str}"
                        item_lines[slot].append(line)

            logger.info(f"Built received list of {sum(len(lines) for lines in item_lines.values())} lines for {len(linked_slots)} slots.")
            if player_table[slot]['online'] is True:
                msg_lines.append(f"\n### {slot} (You're online right now!)")
            elif player_table[slot]['online'] == 0:
                msg_lines.append(f"\n### {slot} (Never logged in)")
            else:
                msg_lines.append(f"\n### {slot} (Last online <t:{int(player_table[slot]['online'])}:R>)")

            if player_table[slot]['goaled'] or player_table[slot]['released']:
                msg_lines.append("-# Finished playing (goaled or released).")
            elif len(item_lines[slot]) == 0:
                msg_lines.append("No new items received since last played.")
            else:
                # add the lists
                msg_lines += item_lines[slot]

            await newpost.edit(content="\n".join(msg_lines))
        except discord.errors.HTTPException as e:
                logger.error(f"Couldn't post received items!",e)
                logger.error(f"Message was {len("\n".join(msg_lines))} chars long.")
                await newpost.edit(content=f"Error: {e}\nShare this message with <@49288117307310080>:\n{"".join(traceback.format_exception(type(e), e, e.__traceback__))}")


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
        api_port = room['flask_port']
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
            return await newpost.edit(content=self.messages['no_slots_linked'])

        # Get the game table
        try:
            game_table = requests.get(f"http://localhost:{api_port}/inspectgame", timeout=10).json()
        except ConnectionError|urllib3.exceptions.MaxRetryError|requests.exceptions.ConnectionError:
            return await newpost.edit(
                content="Couldn't connect to the running Archipelago game. It might be restarting.\nTry again in a minute or two.")

        # Build the hint table
        hint_table = {}
        for slot in linked_slots:
            if slot in game_table['players']:
                if game_table['players'][slot]['goaled'] or game_table['players'][slot]['released']: continue
                hint_table[slot] = {}
                for item in game_table['players'][slot]['hints']['sending']:
                    if item['found'] is True: continue
                    if item['classification'] in ["trap", "filler", "currency"]: continue
                    if any([game_table['players'][item['receiver']]['released'],game_table['players'][item['receiver']]['goaled']]): continue
                    hint_table[slot].update({
                        item['location']: {"item": item['name'],
                                    "sender": item['location']['player'],
                                    "receiver": item['receiver'],
                                    "classification": item['classification'],
                                    "entrance": item['location']['entrance'],
                                    "costs": item['location']['requirements'],
                                    } })
                for item in game_table['players'][slot]['hints']['receiving']:
                    if item['found'] is True: continue
                    if item['classification'] in ["trap", "filler", "currency"]: continue
                    if item['location']['player'] in linked_slots: continue
                    if any([game_table['players'][item['receiver']]['released'],game_table['players'][item['receiver']]['goaled']]): continue
                    hint_table[slot].update({
                        item['location']: {"item": item['name'],
                                           "sender": item['location']['player'],
                                           "receiver": item['receiver'],
                                           "classification": item['classification'],
                                           "entrance": item['location']['entrance'],
                                           "costs": item['location']['requirements'],
                                           }})

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
                    hints_list += f"\n> -# This will require {join_words(hint['Costs'])} to obtain."
            else:
                hints_list += f"\n**{hint['Receiver']}'s {hint['Item']}** is on {hint['Location']}{f" at {hint['Entrance']}" if hint['Entrance'] else ""}."
                if bool(hint['Costs']):
                    hints_list += f"\n> -# This will require {join_words(hint['Costs'])} to obtain."

        hints_list += "\n\n## To Be Found:"
        for hint in hint_table_list:
            if hint["Receiver"] not in linked_slots: continue
            if hint["Sender"] == hint["Receiver"]: continue
            hints_list += f"\n**Your {hint['Item']}** is on {hint['Sender']}'s {hint['Location']}{f" at {hint['Entrance']}" if hint['Entrance'] else ""}."
            if bool(hint['Costs']):
                hints_list += f" (Costs {join_words(hint['Costs'])})"


        await newpost.edit(content=hints_list)

    @aproom.command()
    @app_commands.autocomplete(slot=user_linked_slots_complete)
    @app_commands.describe(slot="Linked slot to upload to", slot_file="File to upload. Run this command without for more info.")
    async def upload_data(self, interaction: discord.Interaction, slot: str, slot_file: discord.Attachment = None):
        """Upload a compatible file to enhance item log tracking."""

        deferpost = await interaction.response.defer(ephemeral=True, thinking=True,)
        newpost = await interaction.original_response()

        if not self.ctx.extras.get('ap_rooms'):
            self.ctx.extras['ap_rooms'] = {}
            self.fetch_guild_room(interaction.guild_id)
            if not self.ctx.extras['ap_rooms'].get(interaction.guild_id):
                return await newpost.edit(content="No Archipelago room is currently set for this server.")

        room = self.ctx.extras['ap_rooms'].get(interaction.guild_id)
        api_port = room['flask_port']
        if not room:
            return await newpost.edit(content="No Archipelago room is currently set for this server.")

        game_table = requests.get(f"http://localhost:{api_port}/inspectgame", timeout=10).json()

        if not game_table:
            return await newpost.edit(content="Couldn't fetch the game table from the running Archipelago game.")

        if slot_file is None:
            match game_table['players'][slot]['game']:
                case "Trackmania":
                    helpmsg = """You can upload a file from Openplanet's PluginStorage to allow metadata such as
                    track names to appear in the item log alongside the relevant track checks.
                    
                    For example, "Series 7 Map 3 - Target Time" could become "S7M3: [Manoa Rush](<https://trackmania.exchange/mapshow/34593>) - Target Time"
                    
                    The file will be located in `C:\\Users\\<Your Windows Username>\\OpenplanetNext\\PluginStorage\\ArchipelagoPlugin\\saves`
                    and is named something like `93762637785644248741_0_9.json`.
                    
                    Note that maps are rolled one series at a time, and not before you've unlocked them - you may have to upload this file
                    multiple times for full tracking, if this matters to you."""
                    return await newpost.edit(content=helpmsg)
                case _:
                    # Not supported
                    helpmsg = f"""{slot}'s game ({game_table['players'][slot]['game']}) doesn't need a file uploaded to it.
                    If metadata *can* be used from a file you have in mind, let Splatsune know so they can integrate it into this script."""

                    return await newpost.edit(content=helpmsg)
        else:
            match game_table['players'][slot]['game']:
                case "Trackmania":
                    up_request = requests.post(f"http://localhost:{api_port}/upload_data/{slot}", files=slot_file.fp)
                    if up_request.status_code == 200:
                        return await newpost.edit(content=f"✓ {up_request.text}")
                    else: return await newpost.edit(content=f"✗ {up_request.status_code}: {up_request.text}")
                case _:
                    # Not supported
                    return await newpost.edit(content="Sorry, I can't use that file - your game doesn't support this.")

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
        room = self.ctx.extras['ap_rooms'].get(guild_id, {})
        if room and room.get('last_activity') and room.get('port'):
            if time.time() - room['last_activity'] < 3600:
                return room
            else:
                # Expire cache after 1 hour
                self.ctx.extras['ap_rooms'][guild_id] = {}
                return False
        elif room and room.get('port'):
            return room
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
                        'port': result[9],
                        'flask_port': result[10],
                    }
                    self.ctx.extras['ap_rooms'][guild_id] = roomdict
                    return roomdict
                else:
                    return {}

    @commands.Cog.listener()
    async def on_ready(self):
        if not self.ctx.procs.get('archipelago'):
            self.ctx.procs['archipelago'] = {
                "channel_archivist": 1450616053204910191,
            }

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
                # logger.info(f"Info: {json.dumps(log)}")
                env = os.environ.copy()

                env['LOG_URL'] = log['log_url']
                env['WEBHOOK_URL'] = log['webhooks'][0] if len(log['webhooks']) > 0 else None
                env['SESSION_COOKIE'] = log['session_cookie']
                env['SPOILER_URL'] = log['spoiler_url'] if log['spoiler_url'] else None
                env['MSGHOOK_URL'] = log['msghooks'][0] if len(log['msghooks']) > 0 else None
                env['METAHOOK_URL'] = log['meta_webhook'] if 'meta_webhook' in log else None

                try:
                    script_path = os.path.join(os.path.dirname(__file__), '..', 'ap_itemlog.py')
                    process = subprocess.Popen([sys.executable, script_path], env=env)
                    self.ctx.procs['archipelago'][log['guild']] = process
                except:
                    logger.error("Error starting log:",exc_info=True)

async def setup(bot):
    logger.info("Loading Archipelago cog extension.")
    await bot.add_cog(Archipelago(bot))

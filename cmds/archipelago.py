import os
import sys
import subprocess
import requests
import logging
import yaml
import traceback
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor
import discord
from discord import app_commands
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands._types import BotT

from cmds.ap_scripts.archilogger import ItemLog

cfg = None

logger = logging.getLogger('discord.ap')

with open('config.yaml', 'r') as file:
    cfg = yaml.safe_load(file)


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
        env = os.environ.copy()
        env['LOG_URL'] = log_url
        env['WEBHOOK_URL'] = webhook
        env['SESSION_COOKIE'] = cfg['bot']['archipelago']['session_cookie']
        env['SPOILER_URL'] = spoiler_url if spoiler_url else None
        env['MSGHOOK_URL'] = log['msghook'] if log['msghook'] else None

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
        self.ctx.extras['ap_channel'] = self.ctx.get_channel(1349546289490034821)
        self.ctx.extras['ap_webhook'] = await self.ctx.extras['ap_channel'].webhooks()
        if len(self.ctx.extras['ap_webhook']) == 1: self.ctx.extras['ap_webhook'] = self.ctx.extras['ap_webhook'][0]

            # Run itemlogs if any are configured
        if len(cfg['bot']['archipelago']['itemlogs']) > 0:
            logger.info("Starting saved itemlog processes.")
            for log in cfg['bot']['archipelago']['itemlogs']:
                logger.info(f"Starting itemlog for guild ID {log['guild']}")
                env = os.environ.copy()
            
                env['LOG_URL'] = log['log_url']
                env['WEBHOOK_URL'] = log['webhook']
                env['SESSION_COOKIE'] = cfg['bot']['archipelago']['session_cookie']
                env['SPOILER_URL'] = log['spoiler_url'] if log['spoiler_url'] else None
                env['MSGHOOK_URL'] = log['msghook'] if log['msghook'] else None
            
                try: 
                    script_path = os.path.join(os.path.dirname(__file__), 'ap_itemlog.py')
                    process = subprocess.Popen([sys.executable, script_path], env=env)
                    itemlog_processes.update({self.ctx.procs['archipelago'][log['guild']]: process.pid})
                except:
                    logger.error("Error starting log:",exc_info=True)

async def setup(bot):
    logger.info("Loading Archipelago cog extension.")
    await bot.add_cog(Archipelago(bot))

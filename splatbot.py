# Import essential libraries
import asyncio
import logging
import functools
import typing

import discord
import yaml
from discord import app_commands
from discord.ext import commands

# setup logging
logger = logging.getLogger('discord')
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('[%(name)s][%(levelname)s] %(message)s'))
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# init vars?
cfg = None

# load config
with open('config.yaml', 'r', encoding='UTF-8') as file:
    cfg = yaml.safe_load(file)

# configure subscribed intents
intents = discord.Intents.default()

class Splatbot(commands.Bot):
    procs: dict = {}
    extras: dict = {}

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
            allowed_contexts=app_commands.AppCommandContext(guild=True, dm_channel=True, private_channel=True),
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True)
        )

    async def setup_hook(self) -> None:
        logger.info("Syncing command tree.")
        self.tree.add_command(extension_reload)
        self.tree.add_command(settings)
        await self.tree.sync()

    def to_thread(self, func: typing.Callable) -> typing.Coroutine:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            return await asyncio.to_thread(func, *args, **kwargs)

        return wrapper


async def load_extensions(bot: commands.Bot):
    for ext in [
        "cmds.archipelago",
        "cmds.raocow",
    ]:
        await bot.load_extension(ext,package=ext)

async def ext_autocomplete(ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
    permitted_values = list(pon.extensions.keys())
    if len(current) == 0:
        return [app_commands.Choice(name=opt.title(),value=opt) for opt in permitted_values]
    else:
        return [app_commands.Choice(name=opt.title(),value=opt) for opt in permitted_values if current in opt.lower()]

@commands.is_owner()
@app_commands.default_permissions(manage_messages=True)
@app_commands.describe(extension="The extension to reload")
@app_commands.autocomplete(extension=ext_autocomplete)
@app_commands.command(name="reload_ext")
async def extension_reload(interaction: discord.Interaction, extension: str):
    try:
        await interaction.client.reload_extension(extension)
        return await interaction.response.send_message(f"All done! Reloaded `{extension}` for ya.",ephemeral=True)
    finally:
        pass

@app_commands.default_permissions(manage_messages=True)
@app_commands.command()
async def settings(interaction: discord.Interaction, log_level: str = None, avatar: discord.Attachment = None):
    """Configure settings for the bot"""
    message_buffer = []
    friendly_names = {
        "avatar": "Avatar",
        "log_level": "Logging Level",
    }

    if not (log_level or avatar):
        await interaction.response.send_message("No changes made.", ephemeral=True)
        return

    if log_level:
        match log_level:
            case "error": logger.setLevel(logging.ERROR)
            case "warning": logger.setLevel(logging.WARNING)
            case "info": logger.setLevel(logging.INFO)
            case "debug": logger.setLevel(logging.DEBUG)
            case "get":
                level = None
                match logger.getEffectiveLevel():
                    case 10: level = "Debug"
                    case 20: level = "Info"
                    case 30: level = "Warning"
                    case 40: level = "Error"
                    case 50: level = "Critical"
                message_buffer.append(f"**{friendly_names['log_level']}:** Currently we are logging at a `{level}` level.")
            case _:
                message_buffer.append(f"**{friendly_names['log_level']}:** `{log_level}` is not an accepted log level.")

        message_buffer.append(f"**{friendly_names['log_level']}:** Set the bot's logging level to `{log_level}`.")

    if avatar:
        # Check if the attachment is an image
        if not avatar.content_type.startswith("image/"):
            message_buffer.append(f"**{friendly_names['avatar']}:** The provided file is not an image.")
        else:
            # Set avatar for Pepper
            await pon.user.edit(avatar=await avatar.read())
            await pon.application.edit(icon=await avatar.read())
            message_buffer.append(f"**{friendly_names['avatar']}:** Successfully set avatar.")

    # Finally send message
    await interaction.response.send_message("\n".join(message_buffer), ephemeral=True)

pon = Splatbot()

@pon.event
async def on_ready():

    logger.info(f"Logged in. I am {pon.user} (ID: {pon.user.id})")

async def main():
    async with pon:
        await load_extensions(pon)
        await pon.start(cfg['bot']['discord_token'])

asyncio.run(main())

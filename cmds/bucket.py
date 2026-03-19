import json
import os
import sys
import subprocess
import requests
import regex as re
import logging
import signal
import yaml
import traceback
import typing
import datetime
import random
import isodate
from io import BytesIO
import psycopg2 as psql
from psycopg2.extras import Json as psql_json
import asyncio
from concurrent.futures import ThreadPoolExecutor
from tabulate import tabulate
from pyyoutube import Api
import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.ext.commands import Context
from discord.ext.commands._types import BotT

from datetime import date, timezone, timedelta as td

cfg = None

logger = logging.getLogger('discord.bucket')

with open('config.yaml', 'r', encoding='UTF-8') as file:
    cfg = yaml.safe_load(file)

sqlcfg = cfg['bot']['psql']
bucketcfg = cfg['bot']['bucket']

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

class Bucket(commands.GroupCog, group_name="bucket"):
    """Bucket is a bot used on XKCD's IRC channel. It stores factoids!
    ...This is our blatant copy.

    Tell it 'Bucket is a good bot' and internally it's stored as:
        the noun (Bucket)
        the 'verb' (is/was/has/<asked>)
        and the factoid ('a good bot')

    It also has a chance to remember and recall things without you
    asking it."""

    nouns = {}
    random_chance: float = 0.35
    rng = lambda x: random.random() < x

    blacklist: list[discord.User]

    def __init__(self, bot):
        self.ctx = bot
        self.random_chance = bucketcfg['random_chance'] if 'random_chance' in bucketcfg else self.random_chance

    async def cog_command_error(self, ctx: Context[BotT], error: Exception) -> None:
        await ctx.reply(f"Command error: {error}",ephemeral=True)

    async def fetch_factoids(self) -> None:
        """Pulls factoids from the database into the local copy."""
        pass

    async def push_factoid(self, noun: str, verb: str, factoid: str) -> bool:
        """Push a new factoid into the database AND the local copy."""
        pass

    @commands.Cog.listener()
    async def on_ready(self):
        # init db if not available
        # fetch nouns and factoids to dict
        pass

    @commands.Cog.listener()
    async def on_message(self, msg):
        # If message from itself, ignore
        # If message mentions the bot, always respond
        # Else, handle if:
            # author is NOT blacklisted, AND
            # a noun is said on its own, or
            # a factoid could be inferred, and random chance rolls true

async def setup(bot):
    logger.info("Loading Bucket cog extension.")
    await bot.add_cog(Bucket(bot))

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
from datetime import timedelta
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
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands._types import BotT

cfg = None

logger = logging.getLogger('discord.raocow')

executor = ThreadPoolExecutor(max_workers=5)

with open('config.yaml', 'r', encoding='UTF-8') as file:
    cfg = yaml.safe_load(file)

sqlcfg = cfg['bot']['archipelago']['psql']
try: 
    sqlcon = psql.connect(
        dbname=sqlcfg['database']['raocow'],
        user=sqlcfg['user'],
        password=sqlcfg['password'] if 'password' in sqlcfg else None,
        host=sqlcfg['host'],
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

class Raocmds(commands.GroupCog, group_name="raocow"):
    """Commands relating to the YouTuber raocow and his content."""

    def __init__(self, bot):
        self.ctx = bot

    async def cog_command_error(self, ctx: Context[BotT], error: Exception) -> None:
        await ctx.reply(f"Command error: {error}",ephemeral=True)

    @app_commands.command()
    async def playlist(self, interaction: discord.Interaction, search: str = None):
        """Fetches a playlist from raocow's channel."""
        await interaction.response.defer(thinking=True,ephemeral=True)

        if not sqlcon:
            await interaction.followup.send("Database connection is not available.",ephemeral=True)
            return

        if search is None:
            with sqlcon.cursor() as cursor:
                cursor.execute("SELECT * FROM playlists ORDER BY RANDOM() LIMIT 1")
                result = cursor.fetchone()

                if not result:
                    await interaction.followup.send("No playlists found in the database.", ephemeral=True)
                    return

                await interaction.followup.send(f"Random Playlist:\n{result[1]}: {result[0]}", ephemeral=True)
            return

        with sqlcon.cursor() as cursor:
            cursor.execute("SELECT * FROM playlists WHERE title ILIKE %s", (f"%{search}%",))
            results = cursor.fetchall()

            if not results:
                await interaction.followup.send("No playlists found.",ephemeral=True)
                return

            # Format the results
            formatted_results = "\n".join([f"{row[1]}: {row[0]}" for row in results])
            await interaction.followup.send(f"Playlists found:\n{formatted_results}",ephemeral=True)

    @commands.is_owner()
    @app_commands.command()
    @app_commands.describe(playlist_count="Number of playlists to fetch (omit for all)",
                           calculate_duration="Calculate the total duration of the playlist",
                           skip_existing="Skip fetching existing playlists")
    async def fetch_playlists(self, interaction: discord.Interaction, playlist_count: int = None, calculate_duration: bool = False, skip_existing: bool = False):
        """Fetches the playlists from raocow's channel and stores them in the database."""
        await interaction.response.defer(thinking=True,ephemeral=True)

        api_key = cfg['bot']['raocow']['yt_api_key']
        channel_ids = [
            "UCjM-Wd2651MWgo0s5yNQRJA" # raocow's channel ID
            # "UCeYAO0Cw3RRwicMZQ2tGD9A" # raoclassic (fan channel with pre-YouTube content)
            # "UC5DLg0WeN4kLbJ8vmJDVAkg" # RaocowGV (Google Video archive)
        ]

        ytc = Api(api_key=api_key)

        def process():
            # Fetch the playlists from raocow's channel (and endorsed fan channels)
            for channel_id in channel_ids:
                playlists = ytc.get_playlists(channel_id=channel_id, count=playlist_count, return_json=True)

                # Store the playlists in the database
                with sqlcon.cursor() as cursor:
                    for item in playlists['items']:
                        # Skip existing playlists
                        if skip_existing: 
                            cursor.execute("SELECT * FROM playlists WHERE playlist_id = %s", (item['id'],))
                            result = cursor.fetchone()
                            if result:
                                logger.info(f"Skipping existing playlist {item['id']}")
                                continue
                        logger.info(f"Fetching playlist {item['id']}")
                        logger.debug(f"Playlist item: {item}")
                        playlist_id = item['id']
                        title = item['snippet']['title']

                        # Get the date of the first video in the playlist
                        # And use as the playlist date
                        video1 = ytc.get_playlist_items(playlist_id=playlist_id, count=None, return_json=True)
                        date = video1['items'][0]['contentDetails']['videoPublishedAt'] if video1 and 'items' in video1 and video1['items'] else None
                        playlist_length = item['contentDetails']['itemCount']
                        duration: str = None

                        if calculate_duration:
                            # Calculate the total duration of the playlist
                            total_duration = timedelta()
                            for video in video1['items']:
                                video_id = video['snippet']['resourceId']['videoId']
                                video_details = ytc.get_video_by_id(video_id=video_id, return_json=True)
                                if 'items' in video_details and len(video_details['items']) > 0:
                                    duration = video_details['items'][0]['contentDetails']['duration']
                                    total_duration += isodate.parse_duration(duration)

                                # Convert total duration to a readable format (e.g., HH:MM:SS)
                                duration = str(total_duration)

                        cursor.execute('''
                                        INSERT INTO playlists (playlist_id, title, datestamp, length, duration) VALUES (%s, %s, %s, %s, %s) ON CONFLICT (playlist_id) DO UPDATE
                                        SET datestamp = EXCLUDED.datestamp, length = EXCLUDED.length, duration = EXCLUDED.duration''',
                                        (playlist_id, title, date, playlist_length, duration)
                                        )
                        sqlcon.commit()
                        logger.info(f"Inserted playlist {playlist_id} into database.")

        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(executor, process)
            await interaction.followup.send("Playlists fetched and stored successfully.",ephemeral=True)
        except Exception as e:
            logger.error(f"Error fetching playlists: {e}",e,exc_info=True)
            await interaction.followup.send(f"An error occurred: {e}",ephemeral=True)


    @commands.Cog.listener()
    async def on_ready(self):
        pass

async def setup(bot):
    logger.info("Loading Raocow cog extension.")
    await bot.add_cog(Raocmds(bot))

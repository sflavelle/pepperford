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
from datetime import date, timezone, timedelta as td

cfg = None

logger = logging.getLogger('discord.raocow')

executor = ThreadPoolExecutor(max_workers=5)

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
    
# Moderator role predicates
def is_mod():
    async def predicate(ctx):
        return ctx.user.get_role(404707268823744524) is not None or ctx.user.get_role(629379744063815688) is not None
    return commands.check(predicate)

class Raocmds(commands.GroupCog, group_name="raocow"):
    """Commands relating to the YouTuber raocow and his content."""

    def __init__(self, bot):
        self.ctx = bot

    async def cog_command_error(self, ctx: Context[BotT], error: Exception) -> None:
        await ctx.reply(f"Command error: {error}",ephemeral=True)

    async def series_autocomplete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        """Autocomplete for the playlist command."""

        results = None

        if not sqlcon:
            return []
        with sqlcon.cursor() as cursor:
            cursor.execute("SELECT series_name FROM pepper.raocow_series order by series_name asc")
            results = cursor.fetchall()

        if len(current) == 0:
            return [app_commands.Choice(name=opt[0][:100],value=opt[0]) for opt in results][:25]
        else:
            return [app_commands.Choice(name=opt[0][:100],value=opt[0]) for opt in results if current.lower() in opt[0].lower()][:25]

    async def playlist_autocomplete(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        """Autocomplete for the playlist command."""

        results = None

        if not sqlcon:
            return []
        with sqlcon.cursor() as cursor:
            cursor.execute("SELECT playlist_id, title, alias FROM pepper.raocow_playlists where visible = 'true' order by datestamp desc")
            results = cursor.fetchall()

        options = []
        for result in results:
            options.append((result[0], result[1], f"{result[1]} ({result[2]})" if bool(result[2]) else result[1]))

        if len(current) == 0:
            return [app_commands.Choice(name=opt[1][:100],value=opt[0]) for opt in options][:25]
        else:
            return [app_commands.Choice(name=opt[1][:100],value=opt[0]) for opt in options if current.lower() in opt[2].lower()][:25]

    async def playlist_autocomplete_all(self, ctx: discord.Interaction, current: str) -> typing.List[app_commands.Choice[str]]:
        """Autocomplete for the playlist command (all videos, including non-visible)."""

        results = None

        if not sqlcon:
            return []
        with sqlcon.cursor() as cursor:
            cursor.execute("SELECT playlist_id, title, alias FROM pepper.raocow_playlists order by datestamp desc")
            results = cursor.fetchall()
        
        options = []
        for result in results:
            options.append((result[0], result[1], f"{result[1]} ({result[2]})" if bool(result[2]) else result[1]))

        if len(current) == 0:
            return [app_commands.Choice(name=opt[1][:100],value=opt[0]) for opt in options][:25]
        else:
            return [app_commands.Choice(name=opt[1][:100],value=opt[0]) for opt in options if current.lower() in opt[2].lower()][:25]

    @app_commands.command()
    @app_commands.autocomplete(search=playlist_autocomplete)
    @app_commands.describe(search="Search for a playlist (leave blank for a random one)",
                           public="Share the playlist with the class?")
    async def playlist(self, interaction: discord.Interaction, search: str = None, public: bool = False):
        """Find a playlist of one of Raocow's series. (or: get a random one!)"""
        await interaction.response.defer(thinking=True,ephemeral=not public)

        result = None

        if not sqlcon:
            await interaction.followup.send("Database connection is not available.",ephemeral=True)
            return

        if search is None:
            logger.info("Playlist: Fetching a random playlist.")
            with sqlcon.cursor() as cursor:
                cursor.execute("SELECT * FROM pepper.raocow_playlists where visible = 'true' ORDER BY RANDOM() LIMIT 1")
                result = cursor.fetchone()

                if not result:
                    logger.error("No playlists found in the database.")
                    await interaction.followup.send("No playlists found in the database.", ephemeral=True)
                    return

                logger.info(f"Playlist: Found random playlist {result[1]} ({result[0]})")
        else:
            with sqlcon.cursor() as cursor:
                if search.startswith("PL") and " " not in search:
                    # Choice returns the playlist ID
                    logger.info(f"Playlist: Searching for playlist ID {search}")
                    cursor.execute("SELECT * FROM pepper.raocow_playlists WHERE playlist_id = %s and visible = 'true'", (search,))
                else:
                    # Search for the playlist title
                    logger.info(f"Playlist: Searching for playlist title matching {search}")
                    cursor.execute("SELECT * FROM pepper.raocow_playlists WHERE title ILIKE %s and visible = 'true' order by datestamp desc", (search,))
                result = cursor.fetchone()

                if not result:
                    logger.error(f"No playlists found matching {search}")
                    await interaction.followup.send("No playlists found.",ephemeral=True)
                    return

        # Format the results
        id, title, datestamp, length, duration, visibility, thumbnail, game_link, latest_video, alias, series, channel_id = result

        date_string: str = None
        ongoing = False

        if latest_video:
            try:
                ONGOING_SERIES_THRESHOLD = td(days=3)

                # Parse the latest_video and datestamp as datetime objects
                now = date.today()

                if now - latest_video <= ONGOING_SERIES_THRESHOLD:
                    ongoing = True
                    date_string = f"{datestamp} - Ongoing"
                else:
                    date_string = f"{datestamp} - {latest_video}"
            except Exception as e:
                logger.error(f"Error parsing playlist dates: {e}")
        else:
            date_string = str(datestamp)

        pl_embed = discord.Embed(
            title=title,
            description=None,
            color=discord.Color.red()
        )
        if ongoing:
            pl_embed.description = "-# This series is ongoing - the data for this playlist may not be up to date."
        pl_embed.add_field(name="Playlist Link", value=f"https://www.youtube.com/playlist?list={id}", inline=False)
        if game_link:
            pl_embed.add_field(name="Game Link(s)", value=game_link, inline=False)
        pl_embed.add_field(name="Videos", value=length, inline=True)
        pl_embed.add_field(name="Date(s)", value=date_string, inline=True)
        pl_embed.add_field(name="Duration", value=duration if duration else "N/A", inline=True)
        if alias:
            pl_embed.add_field(name="Alias (also known as)", value=alias, inline=False)
        if thumbnail:
            pl_embed.set_thumbnail(url=thumbnail)
        # pl_embed.set_footer(text="raocow on youtube: https://www.youtube.com/@raocow")
        if series:
            pl_embed.set_footer(text=f"a game in the {series} series")

        logger.info(f"Playlist: Found playlist {title} ({id}), sending")
        await interaction.followup.send(embed=pl_embed,ephemeral=not public)

    @is_mod()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.autocomplete(search=playlist_autocomplete_all)
    @app_commands.describe(search="Search for a playlist",
                           new_title="New title for the playlist",
                           new_datestamp="New date for the playlist",
                           new_game_link="New game link(s) (\\n separated)",
                           visible="Make the playlist visible to users")
    @app_commands.command()
    async def tweak_playlist(self, interaction: discord.Interaction,
                             search: str, new_title: str = None, new_datestamp: str = None,
                             visible: bool = None, new_game_link: str = None):
        """Edit a playlist in Pepper's database with new information."""
        await interaction.response.defer(thinking=True,ephemeral=True)

        if not sqlcon:
            await interaction.followup.send("Database connection is not available.",ephemeral=True)
            return

        search_result = None

        with sqlcon.cursor() as cursor:
            # Update the playlist in the database
            cursor.execute(f'''
                UPDATE pepper.raocow_playlists
                SET title = COALESCE(%s, title),
                    datestamp = COALESCE(%s, datestamp),
                    visible = COALESCE(%s, visible),
                    game_link = COALESCE({"E%s" if new_game_link else "%s"}, game_link)
                WHERE playlist_id = %s
                RETURNING *
            ''', (new_title, new_datestamp, visible, new_game_link, search))
            sqlcon.commit()
            search_result = cursor.fetchone()

        id, new_title, datestamp, length, duration, visibility, thumbnail, game_link, latest_video, alias, series, channel_id = search_result

        message = f"Playlist {title} updated successfully.\n"
        for param in ["new_title", "new_datestamp", "visible", "new_game_link"]:
            if param is not None:
                message += f"\n{param.replace('_', ' ').capitalize()}: {param}"

        await interaction.followup.send(f"Playlist {title} updated successfully.",ephemeral=True)

    @app_commands.command()
    @app_commands.autocomplete(series_name=series_autocomplete)
    @app_commands.describe(series_name="The series to fetch playlists for")
    async def series(self, interaction: discord.Interaction, series_name: str, public: bool = False):
        """Get a list of playlists for a specific series."""
        await interaction.response.defer(thinking=True,ephemeral=not public)

        if not sqlcon:
            await interaction.followup.send("Database connection is not available.",ephemeral=not public)
            return

        with sqlcon.cursor() as cursor:
            cursor.execute("SELECT * FROM pepper.raocow_playlists WHERE series = %s and visible = 'true' ORDER BY datestamp ASC", (series_name,))
            results = cursor.fetchall()

        if not results:
            await interaction.followup.send(f"No playlists found for series '{series_name}'.",ephemeral=not public)
            return


        playlist_strings = []

        playlist_strings.append(f"## Playlists for Series: {series_name}")

        for result in results:
            id, title, datestamp, length, duration, visibility, thumbnail, game_link, latest_video, alias, series, channel_id = result
            date_string = f"{datestamp} - {latest_video}" if latest_video else str(datestamp)

            playlist_strings.append(f"- **[{title}](https://www.youtube.com/playlist?list={id})** - Date(s): {datestamp} / {length} videos")

        # If the full message exceeds Discord's character limit, truncate it
        if len("\n".join(playlist_strings)) > 2000:
            playlist_strings = playlist_strings[:15]  # Limit to first 15 playlists
            playlist_strings.append("\n... (truncated, too many playlists)")

        await interaction.followup.send("\n".join(playlist_strings),ephemeral=not public)

    @is_mod()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.command()
    @app_commands.describe(playlist_count="Number of playlists to fetch (omit for all)",
                           calculate_duration="Calculate the total duration of the playlist (EXPENSIVE API USE)",
                           skip_duration_calculated="Skip playlists that already have their duration calculated",
                           skip_existing="Skip fetching existing playlists")
    async def fetch_playlists(self, interaction: discord.Interaction,
        playlist_count: int = None,
        include_fanchannels: bool = False,
        calculate_duration: bool = False,
        skip_duration_calculated: bool = False,
        skip_existing: bool = False):
        """Pulls playlists from raocow's channel (and optionally endorsed fan channels)."""
        await interaction.response.defer(thinking=True,ephemeral=True)

        api_key = cfg['bot']['raocow']['yt_api_key']
        channel_ids = [
            "UCjM-Wd2651MWgo0s5yNQRJA" # raocow's channel ID    
        ]

        if include_fanchannels:
            channel_ids = channel_ids + [
                "UCKnEkwBqrai2GB6Rxl1OqCA" # raolists (fan channel with playlists)
                # "UC5DLg0WeN4kLbJ8vmJDVAkg" # RaocowGV (Google Video archive)
                # "UCeYAO0Cw3RRwicMZQ2tGD9A" # raoclassic (fan channel with pre-YouTube content)
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
                            cursor.execute("SELECT * FROM pepper.raocow_playlists WHERE playlist_id = %s", (item['id'],))
                            result = cursor.fetchone()
                            if result:
                                logger.info(f"Skipping existing playlist {item['id']}")
                                continue
                        # Skip playlists that already have their duration calculated
                        if skip_duration_calculated:
                            cursor.execute("SELECT * FROM pepper.raocow_playlists WHERE playlist_id = %s and duration is not null", (item['id'],))
                            result = cursor.fetchone()
                            if result:
                                logger.info(f"Skipping playlist {item['id']} (duration already calculated)")
                                continue
                        # Skip Favorites playlist
                        if item['id'].startswith("FL"): continue
                        logger.info(f"Fetching playlist {item['id']}")
                        logger.debug(f"Playlist item: {item}")
                        playlist_id = item['id']
                        title = item['snippet']['title']

                        try:
                            if channel_id == "UCKnEkwBqrai2GB6Rxl1OqCA":
                                # Raolists prefixes every playlist with a number
                                # Remove the number prefix from the title
                                title = title.split('.', 1)[-1].lstrip() if '. ' in title else title

                            # Get the date of the first video in the playlist
                            # And use as the playlist date
                            pl_videos = ytc.get_playlist_items(playlist_id=playlist_id, count=None, return_json=True)
                            logger.info(f"Playlist {playlist_id} first video: {pl_videos['items'][0]['snippet']['title']}")
                            first_id = pl_videos['items'][0]['snippet']['resourceId']['videoId']

                            # For fan-channels:
                            # Make sure this playlist is not already uploaded by raocow himself
                            if channel_id in channel_ids[1:]:
                                cursor.execute('SELECT video_id, playlist_id, channel_id from pepper.raocow_videos where playlist_id = %s and video_id = %s', (channel_ids[0], first_id))
                                query_exists = cursor.fetchall()
                                if bool(query_exists):
                                    logger.warning(f"Playlist uploaded already by official channel, skipping.")
                                    continue


                            date = pl_videos['items'][0]['contentDetails']['videoPublishedAt'] if pl_videos else None
                            latest_date = None
                            playlist_length = item['contentDetails']['itemCount']
                            thumbnail = item['snippet']['thumbnails']['high']['url'] if 'thumbnails' in item['snippet'] else None
                            duration = None

                            for v in pl_videos['items']:
                                if v['status']['privacyStatus'] in ['private', 'unlisted']:
                                        continue
                                vdate = v['contentDetails']['videoPublishedAt'] if 'videoPublishedAt' in v['contentDetails'] else v['snippet']['publishedAt']
                                vid = v['snippet']['resourceId']['videoId']
                                pid = item['id']
                                vtitle = v['snippet']['title']

                                if channel_id == "UCKnEkwBqrai2GB6Rxl1OqCA":
                                    # 'Originally Uploaded - 6/9/07'
                                    # Match a date string in the description
                                    logger.info(f"Fan channel: searching for date in description of video {vid}")
                                    if 'description' in v['snippet'] and v['snippet']['description']:
                                        if match := re.search(r'(\d{1,2}/\d{1,2}/\d{2,4})', v['snippet']['description']):
                                            vdate = match.group(1)
                                            try:
                                                vdate = date.fromisoformat(vdate.replace('/', '-'))
                                                logger.info(f"Found date {vdate} in video {vid}")
                                            except ValueError:
                                                logger.error(f"Invalid date format in video {vid}: {vdate}")
                                                vdate = None

                                cursor.execute('INSERT INTO pepper.raocow_videos (video_id, playlist_id, title, datestamp, channel_id) VALUES (%s, %s, %s, %s, %s)'
                                'ON CONFLICT (video_id) DO NOTHING', (vid, pid, vtitle, vdate, channel_id))

                            if 'videoPublishedAt' in pl_videos['items'][-1]['contentDetails']:
                                latest_date = pl_videos['items'][-1]['contentDetails']['videoPublishedAt']
                            else:
                                for item in sorted(pl_videos['items'], key=lambda x: x['snippet']['position'], reverse=True):
                                    if item['status']['privacyStatus'] in ['private', 'unlisted']:
                                        continue

                                    if item['contentDetails']['videoPublishedAt']:
                                        latest_date = item['contentDetails']['videoPublishedAt']
                                        break

                            if calculate_duration:
                                # Calculate the total duration of the playlist
                                total_duration = timedelta()
                                for video in pl_videos['items']:
                                    video_id = video['snippet']['resourceId']['videoId']
                                    video_details = ytc.get_video_by_id(video_id=video_id, return_json=True)
                                    if 'items' in video_details and len(video_details['items']) > 0:
                                        duration = video_details['items'][0]['contentDetails']['duration']
                                        total_duration += isodate.parse_duration(duration)

                                    # Convert total duration to a readable format (e.g., HH:MM:SS)
                                    duration = str(total_duration)

                            cursor.execute('''
                                            INSERT INTO pepper.raocow_playlists (playlist_id, title, datestamp, length, duration, thumbnail, latest_video, channel_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) 
                                            ON CONFLICT (playlist_id) DO UPDATE
                                            SET datestamp = EXCLUDED.datestamp, length = EXCLUDED.length, duration = COALESCE(EXCLUDED.duration, pepper.raocow_playlists.duration),
                                            visible = COALESCE(pepper.raocow_playlists.visible, EXCLUDED.visible),
                                            thumbnail = EXCLUDED.thumbnail, latest_video = EXCLUDED.latest_video, channel_id = EXCLUDED.channel_id''',
                                            (playlist_id, title, date, playlist_length, duration, thumbnail, latest_date, channel_id)
                                            )
                            sqlcon.commit()
                            logger.info(f"Inserted playlist {playlist_id} into database.")
                        except Exception as e:
                            logger.error(f"Error processing playlist {playlist_id}: {e}", e, exc_info=True)
                            continue

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

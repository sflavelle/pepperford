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

logger = logging.getLogger('discord.raocow')

executor = ThreadPoolExecutor(max_workers=5)

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
    
def length_from_seconds(seconds) -> str:
    """Convert seconds to a human-readable format."""
    days = 0
    hours = 0
    minutes = 0
    seconds = int(seconds)

    if seconds is None:
        return "N/A"
    minutes = (seconds // 60) % 60
    hours = (seconds // 60 // 60) % 24
    days = hours // 24
    
    if days > 0:
        return f"{days} day{'s' if days > 1 else ''}, {hours:02}:{minutes:02}:{seconds % 60:02}"
    else:
        return f"{hours:02}:{minutes:02}:{seconds % 60:02}"

    
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
        pl_embed.add_field(name="Duration", value=length_from_seconds(duration) if duration else "N/A", inline=True)
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

        embed = discord.Embed(
            title=series_name,
            color=discord.Color.red()
        )

        for result in results:
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

            value = f"[YouTube](https://www.youtube.com/playlist?list={id}) / {datestamp} / {length} videos / {length_from_seconds(duration)}"
            embed.add_field(name=title, value=value, inline=False)

        # If the embed would have too many fields, truncate
        if len(embed.fields) > 25:
            embed.clear_fields()

            for result in results[:25]:
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

                value = f"[YouTube](https://www.youtube.com/playlist?list={id}) / {datestamp} / {length} videos / {length_from_seconds(duration)}"
                embed.add_field(name=title, value=value, inline=False)
                embed.set_footer(text=f"...truncated (25 of {len(results)} playlists shown)")

        await interaction.followup.send(embed=embed, ephemeral=not public)

    @is_mod()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.command()
    @app_commands.autocomplete(playlist_search=playlist_autocomplete_all)
    @app_commands.describe(playlist_count="Number of playlists to fetch (omit for all)",
                           playlist_search="Update a specific playlist (leave blank for all)",
                           include_fanchannels="Include playlists from fan channels (raolists, raoclassic, RaocowGV)",
                           calculate_duration="Calculate the total duration of the playlist (EXPENSIVE API USE)",
                           skip_duration_calculated="Skip playlists that already have their duration calculated",
                           skip_existing="Skip fetching existing playlists")
    async def fetch_playlists(self, interaction: discord.Interaction,
        playlist_search: str = None,
        playlist_count: int = None,
        include_fanchannels: bool = False,
        calculate_duration: bool = False,
        skip_duration_calculated: bool = False,
        skip_existing: bool = False):
        """Pulls playlists from raocow's channel (and optionally endorsed fan channels)."""

        if bool(playlist_search) and bool(playlist_count):
            await interaction.response.send_message("You cannot specify both a playlist search and a playlist count.", ephemeral=True)
            return

        await interaction.response.defer(thinking=True,ephemeral=True)

        # Delegate to synchronous helper functions executed in the threadpool
        if bool(playlist_search):
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(executor, self._process_single_playlist_sync, playlist_search, calculate_duration)
                await interaction.followup.send(f"Updated playlist `{playlist_search}` in the database.", ephemeral=True)
            except Exception as e:
                logger.error(f"Error fetching playlist: {e}",e,exc_info=True)
                await interaction.followup.send(f"An error occurred: {e}",ephemeral=True)
        else:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(executor, self._process_channels_sync, playlist_count, include_fanchannels, calculate_duration, skip_duration_calculated, skip_existing)
                await interaction.followup.send("Playlists fetched and stored successfully.",ephemeral=True)
            except Exception as e:
                logger.error(f"Error fetching playlists: {e}",e,exc_info=True)
                await interaction.followup.send(f"An error occurred: {e}",ephemeral=True)

    def _process_single_playlist_sync(self, playlist_id, calculate_duration=False):
        api_key = cfg['bot']['raocow']['yt_api_key']
        ytc = Api(api_key=api_key)
        channel_ids = ["UCjM-Wd2651MWgo0s5yNQRJA"]

        try:
            # Fetch playlist metadata
            query = ytc.get_playlist_by_id(playlist_id=playlist_id, return_json=True)
            item = query['items'][0] if 'items' in query and len(query['items']) > 0 else None

            pl_videos = ytc.get_playlist_items(playlist_id=playlist_id, count=None, return_json=True)
            first_title = pl_videos['items'][0]['snippet']['title'] if pl_videos and pl_videos.get('items') else 'unknown'
            logger.info(f"Playlist {playlist_id} first video: {first_title}")

            with sqlcon.cursor() as cursor:
                # Use the playlist item to determine channel and other metadata when possible
                channel_id = item['snippet'].get('channelId') if item and 'snippet' in item else None

                # Determine playlist-level fields
                date = pl_videos['items'][0]['contentDetails']['videoPublishedAt'] if pl_videos else None
                latest_date = None
                playlist_length = item['contentDetails']['itemCount'] if item and 'contentDetails' in item else None
                thumbnail = item['snippet']['thumbnails']['high']['url'] if item and 'snippet' in item and 'thumbnails' in item['snippet'] else None

                for v in pl_videos['items']:
                    if v['status']['privacyStatus'] in ['private', 'unlisted']:
                        continue
                    vdate = v['contentDetails'].get('videoPublishedAt') if 'contentDetails' in v else v['snippet'].get('publishedAt')
                    vid = v['snippet']['resourceId']['videoId']
                    pid = playlist_id
                    vtitle = v['snippet']['title']

                    cursor.execute('INSERT INTO pepper.raocow_videos (video_id, playlist_id, title, datestamp, channel_id) VALUES (%s, %s, %s, %s, %s) '
                                   'ON CONFLICT (video_id) DO UPDATE SET datestamp = COALESCE(EXCLUDED.datestamp, pepper.raocow_videos.datestamp)', (vid, pid, vtitle, vdate, channel_id))

                # Find latest date
                if 'videoPublishedAt' in pl_videos['items'][-1].get('contentDetails', {}):
                    latest_date = pl_videos['items'][-1]['contentDetails']['videoPublishedAt']
                else:
                    for item in sorted(pl_videos['items'], key=lambda x: x['snippet']['position'], reverse=True):
                        if item['status']['privacyStatus'] in ['private', 'unlisted']:
                            continue
                        if item['contentDetails'].get('videoPublishedAt'):
                            latest_date = item['contentDetails']['videoPublishedAt']
                            break

                # Calculate durations if requested
                if calculate_duration:
                    for video in pl_videos['items']:
                        video_id = video['snippet']['resourceId']['videoId']
                        video_details = ytc.get_video_by_id(video_id=video_id, return_json=True)
                        if 'items' in video_details and len(video_details['items']) > 0:
                            dur = isodate.parse_duration(video_details['items'][0]['contentDetails']['duration'])
                            duration_sec = dur.total_seconds() if dur is not None else None
                            if duration_sec is not None:
                                cursor.execute('UPDATE pepper.raocow_videos SET duration = %s WHERE video_id = %s', (duration_sec, video_id))

                # Upsert playlist
                cursor.execute('''
                                INSERT INTO pepper.raocow_playlists (playlist_id, title, datestamp, length, thumbnail, latest_video, channel_id) VALUES (%s, %s, %s, %s, %s, %s, %s) 
                                ON CONFLICT (playlist_id) DO UPDATE
                                SET datestamp = EXCLUDED.datestamp, length = EXCLUDED.length,
                                visible = COALESCE(pepper.raocow_playlists.visible, EXCLUDED.visible),
                                thumbnail = EXCLUDED.thumbnail, latest_video = EXCLUDED.latest_video, channel_id = EXCLUDED.channel_id''',
                                (playlist_id, item['snippet'].get('title') if item else None, date, playlist_length, thumbnail, latest_date, channel_id)
                                )

                # Update playlist duration using aggregated video durations
                cursor.execute('''
                                UPDATE pepper.raocow_playlists
                                SET duration = sub.duration
                                FROM (
                                    SELECT playlist_id, SUM(duration) AS duration
                                    FROM pepper.raocow_videos
                                    WHERE playlist_id = %s
                                    GROUP BY playlist_id
                                ) AS sub
                                WHERE pepper.raocow_playlists.playlist_id = sub.playlist_id
                                ''', (playlist_id,))
                sqlcon.commit()
                logger.info(f"Inserted/updated playlist {playlist_id} into database.")

        except Exception as e:
            logger.error(f"Error processing playlist {playlist_id}: {e}", exc_info=True)

    def _process_channels_sync(self, playlist_count=None, include_fanchannels: bool = False, calculate_duration: bool = False, skip_duration_calculated: bool = False, skip_existing: bool = False):
        api_key = cfg['bot']['raocow']['yt_api_key']
        channel_ids = ["UCjM-Wd2651MWgo0s5yNQRJA"]
        if include_fanchannels:
            channel_ids = channel_ids + ["UCKnEkwBqrai2GB6Rxl1OqCA"]

        ytc = Api(api_key=api_key)

        for channel_id in channel_ids:
            try:
                playlists = ytc.get_playlists(channel_id=channel_id, count=playlist_count, return_json=True)
            except Exception as e:
                logger.error(f"Error fetching playlists for channel {channel_id}: {e}", exc_info=True)
                continue

            with sqlcon.cursor() as cursor:
                for item in playlists.get('items', []):
                    try:
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
                        if item['id'].startswith("FL"):
                            continue

                        playlist_id = item['id']
                        title = item['snippet']['title']

                        # Get videos for playlist
                        pl_videos = ytc.get_playlist_items(playlist_id=playlist_id, count=None, return_json=True)
                        logger.info(f"Playlist {playlist_id} first video: {pl_videos['items'][0]['snippet']['title']}")
                        first_id = pl_videos['items'][0]['snippet']['resourceId']['videoId']

                        # For fan-channels: ensure this playlist isn't already uploaded by official channel
                        if channel_id in channel_ids[1:]:
                            cursor.execute('SELECT video_id, playlist_id, channel_id from pepper.raocow_videos where playlist_id = %s and video_id = %s', (channel_ids[0], first_id))
                            query_exists = cursor.fetchall()
                            if bool(query_exists):
                                logger.warning(f"Playlist uploaded already by official channel, skipping.")
                                continue

                        date = pl_videos['items'][0]['contentDetails'].get('videoPublishedAt') if pl_videos else None
                        latest_date = None
                        playlist_length = item['contentDetails']['itemCount'] if 'contentDetails' in item else None
                        thumbnail = item['snippet']['thumbnails']['high']['url'] if 'thumbnails' in item['snippet'] else None

                        for v in pl_videos['items']:
                            if v['status']['privacyStatus'] in ['private', 'unlisted']:
                                continue
                            vdate = v['contentDetails'].get('videoPublishedAt') if 'contentDetails' in v else v['snippet'].get('publishedAt')
                            vid = v['snippet']['resourceId']['videoId']
                            pid = item['id']
                            vtitle = v['snippet']['title']

                            cursor.execute('INSERT INTO pepper.raocow_videos (video_id, playlist_id, title, datestamp, channel_id) VALUES (%s, %s, %s, %s, %s) '
                                           'ON CONFLICT (video_id) DO UPDATE SET datestamp = COALESCE(EXCLUDED.datestamp, pepper.raocow_videos.datestamp)', (vid, pid, vtitle, vdate, channel_id))

                        # Determine latest_date
                        if 'videoPublishedAt' in pl_videos['items'][-1].get('contentDetails', {}):
                            latest_date = pl_videos['items'][-1]['contentDetails']['videoPublishedAt']
                        else:
                            for it in sorted(pl_videos['items'], key=lambda x: x['snippet']['position'], reverse=True):
                                if it['status']['privacyStatus'] in ['private', 'unlisted']:
                                    continue
                                if it['contentDetails'].get('videoPublishedAt'):
                                    latest_date = it['contentDetails']['videoPublishedAt']
                                    break

                        if calculate_duration:
                            for video in pl_videos['items']:
                                video_id = video['snippet']['resourceId']['videoId']
                                video_details = ytc.get_video_by_id(video_id=video_id, return_json=True)
                                if 'items' in video_details and len(video_details['items']) > 0:
                                    dur = isodate.parse_duration(video_details['items'][0]['contentDetails']['duration'])
                                    duration_sec = dur.total_seconds() if dur is not None else None
                                    if duration_sec is not None:
                                        cursor.execute('UPDATE pepper.raocow_videos SET duration = %s WHERE video_id = %s', (duration_sec, video_id))

                        # Upsert playlist
                        cursor.execute('''
                                        INSERT INTO pepper.raocow_playlists (playlist_id, title, datestamp, length, thumbnail, latest_video, channel_id) VALUES (%s, %s, %s, %s, %s, %s, %s) 
                                        ON CONFLICT (playlist_id) DO UPDATE
                                        SET datestamp = EXCLUDED.datestamp, length = EXCLUDED.length,
                                        visible = COALESCE(pepper.raocow_playlists.visible, EXCLUDED.visible),
                                        thumbnail = EXCLUDED.thumbnail, latest_video = EXCLUDED.latest_video, channel_id = EXCLUDED.channel_id''',
                                        (playlist_id, title, date, playlist_length, thumbnail, latest_date, channel_id)
                                        )
                        # Update playlist duration from videos
                        cursor.execute('''
                                       UPDATE pepper.raocow_playlists
                                       SET duration = sub.duration
                                       FROM (
                                           SELECT playlist_id, SUM(duration) AS duration
                                           FROM pepper.raocow_videos
                                           WHERE playlist_id = %s
                                           GROUP BY playlist_id
                                       ) AS sub
                                       WHERE pepper.raocow_playlists.playlist_id = sub.playlist_id
                                       ''', (playlist_id,))
                        sqlcon.commit()
                        logger.info(f"Inserted playlist {playlist_id} into database.")
                    except Exception as e:
                        logger.error(f"Error processing playlist {item.get('id') if isinstance(item, dict) else item}: {e}", exc_info=True)
                        continue

    @tasks.loop(hours=8)
    async def scheduled_fetch_playlists(self):
        # Use configured defaults tuned for periodic runs
        # Support new schema under bot.raocow.auto_fetch, with backward compatibility
        raocow_cfg = cfg.get('bot', {}).get('raocow', {})
        if 'auto_fetch' in raocow_cfg:
            af = raocow_cfg.get('auto_fetch', {})
            enabled = af.get('enabled', True)
            playlist_count = af.get('num_playlists', 3)
            calculate_duration = af.get('calc_duration', True)
        else:
            # Backwards compatibility to older keys
            enabled = raocow_cfg.get('auto_fetch_playlists', True)
            playlist_count = raocow_cfg.get('scheduled_playlist_count', 3)
            calculate_duration = raocow_cfg.get('scheduled_calculate_duration', True)

        if not enabled:
            return
        if not sqlcon:
            logger.warning("Database not available, skipping scheduled playlist fetch.")
            return
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(executor, self._process_channels_sync, playlist_count, True, calculate_duration, False, False)
            logger.info("Scheduled playlist fetch completed.")
        except Exception as e:
            logger.error(f"Error in scheduled playlist fetch: {e}", exc_info=True)


    @commands.Cog.listener()
    async def on_ready(self):
        # Start scheduled task if enabled in config
        try:
            raocow_cfg = cfg.get('bot', {}).get('raocow', {})
            if 'auto_fetch' in raocow_cfg:
                enabled = raocow_cfg.get('auto_fetch', {}).get('enabled', True)
            else:
                enabled = raocow_cfg.get('auto_fetch_playlists', True)

            if enabled and not self.scheduled_fetch_playlists.is_running():
                self.scheduled_fetch_playlists.start()
                logger.info("Started scheduled playlist fetch task.")
        except Exception:
            logger.exception("Failed to start scheduled playlist fetch task.")

    @is_mod()
    @app_commands.default_permissions(manage_messages=True)
    @app_commands.command(name="auto_fetch")
    @app_commands.describe(enabled="Enable or disable scheduled fetching",
                           num_playlists="Number of playlists to fetch on scheduled runs",
                           calc_duration="Whether scheduled runs should calculate video durations")
    async def auto_fetch(self, interaction: discord.Interaction, enabled: typing.Optional[bool] = None, num_playlists: typing.Optional[int] = None, calc_duration: typing.Optional[bool] = None):
        """Control the Raocow playlist auto-fetcher"""
        await interaction.response.defer(thinking=True, ephemeral=True)

        # Ensure config structure exists
        bot_cfg = cfg.setdefault('bot', {})
        raocow_cfg = bot_cfg.setdefault('raocow', {})
        auto_cfg = raocow_cfg.setdefault('auto_fetch', {})

        changed = []
        if enabled is not None:
            auto_cfg['enabled'] = bool(enabled)
            changed.append(f"enabled={enabled}")
        if num_playlists is not None:
            auto_cfg['num_playlists'] = int(num_playlists)
            changed.append(f"num_playlists={num_playlists}")
        if calc_duration is not None:
            auto_cfg['calc_duration'] = bool(calc_duration)
            changed.append(f"calc_duration={calc_duration}")

        # Persist to config.yaml
        try:
            with open('config.yaml', 'w', encoding='UTF-8') as fh:
                yaml.safe_dump(cfg, fh, sort_keys=False)
        except Exception as e:
            logger.error(f"Failed to write config.yaml: {e}", exc_info=True)
            await interaction.followup.send(f"Failed to update configuration: {e}", ephemeral=True)
            return

        # Start/stop scheduler based on enabled
        if 'enabled' in auto_cfg:
            if auto_cfg['enabled'] and not self.scheduled_fetch_playlists.is_running():
                self.scheduled_fetch_playlists.start()
            if not auto_cfg['enabled'] and self.scheduled_fetch_playlists.is_running():
                self.scheduled_fetch_playlists.cancel()

        await interaction.followup.send(f"Updated auto fetch settings: {', '.join(changed) if changed else 'no changes provided' }", ephemeral=True)

async def setup(bot):
    logger.info("Loading Raocow cog extension.")
    await bot.add_cog(Raocmds(bot))

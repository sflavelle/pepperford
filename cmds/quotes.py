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
from discord.ext import commands
from discord.ext.commands import Context
from discord.ext.commands._types import BotT

from cmds.quote_helpers.quoting import *

from datetime import date, timezone, timedelta as td

cfg = None

logger = logging.getLogger('discord.quotes')

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

qcfg = cfg['bot']['quoting']
qvote_timeout = qcfg['vote_timeout']

class Quotes(commands.GroupCog, group_name="quote"):
    """Save or recall memorable messages."""

    def __init__(self, bot):
        self.ctx = bot

    async def cog_command_error(self, ctx: Context[BotT], error: Exception) -> None:
        await ctx.reply(f"Command error: {error}",ephemeral=True)

    
    @app_commands.command(name="get")
    @app_commands.describe(	all_servers="When posting your own quotes in other servers, allow quotes from anywhere.")
    async def quote_get(self, interaction: discord.Interaction, user: discord.User=None, all_servers: bool = False):
        """Get a random quote!"""

        deferpost = await interaction.response.defer(thinking=True,)
        newpost = await interaction.original_response()
        
        try:
            if bool(user) and all_servers and not interaction.user.id == 49288117307310080:
                if user.id != interaction.user.id:
                    await newpost.edit(content=
                        ":no_entry_sign: Just FYI, `all_servers` will only work if you're exposing yourself.")
                    return
                qid,content,aID,aName,timestamp,karma,source = random_quote(None, user.id)
            elif all_servers:
                if interaction.user.id == 49288117307310080:
                    qid,content,aID,aName,timestamp,karma,source = random_quote(None, None)
                else:
                    qid,content,aID,aName,timestamp,karma,source = random_quote(None, user.id)
                    # await newpost.edit(content=
                    # ":no_entry_sign: Just FYI, `all_servers` will only work if you're exposing yourself.")
                    # return
            elif isinstance(interaction.channel, discord.abc.PrivateChannel) and bool(user):
                qid,content,aID,aName,timestamp,karma,source = random_quote(None, user.id)
            elif bool(user):
                qid,content,aID,aName,timestamp,karma,source = random_quote(interaction.guild_id, user.id)
            elif isinstance(interaction.channel, discord.abc.PrivateChannel):
                # Discord can't support this for user apps!
                # Reason being, it cannot access the list of recipients in a channel, in a user app context
                # Which is totally fine, but I have to let the user know that they need to specify
                # who they want a quote of
                await newpost.edit(content=
                    ":no_entry_sign: You'll need to specify a user when getting quotes in a private channel.\n"
                    "This is because Discord doesn't support getting the list of users when you install the app to your account,"
                    " which is *good* because it means apps like this one can't harvest your data willy-nilly!\n"
                    "So for now, just remember to select a user you want a quote from."
                    )
                return
            else:
                qid,content,aID,aName,timestamp,karma,source = random_quote(interaction.guild_id, None)
        except LookupError as error:
            await newpost.edit(content=str(error))
            return
        except Exception as error:
            logger.exception(error)
            return
        
        # Is the user still in the server?
        authorObject = None
        authorAvatar = None
        try: 
            authorObject = await interaction.guild.fetch_member(aID)
        except:
            pass
            
        if bool(authorObject):
            authorAvatar = authorObject.display_avatar
        else:
            author = await self.ctx.fetch_user(aID)
            if author:
                aName = author.name
                authorAvatar = author.display_avatar
            else: 
                aName = rename_user(aID, "'unknown', yeah, let's go with that")
        
        quoteview = discord.Embed(
            description=format_quote(content, timestamp, authorID=aID if authorObject is not None else None, authorName=aName, source=source, format='markdown')
        )
        
        # Set avatar
        if bool(authorAvatar): quoteview.set_thumbnail(url=authorAvatar.url)
        else: quoteview.set_thumbnail(url="https://cdn.thegeneral.chat/sanford/special-avatars/sanford-quote-noicon.png")
        
        if qcfg['voting']['enable'] is True and interaction.is_guild_integration():
          if str(interaction.guild_id) in qcfg['voting'] and qcfg['voting'][str(interaction.guild_id)] is True:
            quoteview.set_footer(text=f"Score: {'+' if karma > 0 else ''}{karma}. Voting is open for {qvote_timeout} minutes.")
          else:
            logger.warning(f"Did not enable voting for quote {qid} in guild {interaction.guild_id}. Guild voting: {qcfg['voting'][str(interaction.guild_id)] if str(interaction.guild_id) in qcfg['voting'] else 'not set'}")
        else:
            logger.warning(f"Did not enable voting for quote {qid} in guild {interaction.guild_id}. Global Voting: {qcfg['voting']['enable']}. Running as guild integration: {interaction.is_guild_integration()}")
        # Send the resulting quote
        await newpost.edit(allowed_mentions=discord.AllowedMentions.none(),embed=quoteview)
        logger.info(f"Quote {qid} requested by {interaction.user} ({interaction.user.id}) in guild {interaction.guild_id} ({interaction.guild.name if interaction.guild else 'DM'})")
        
        if qcfg['voting']['enable'] is True and interaction.is_guild_integration():
          if str(interaction.guild_id) in qcfg['voting'] and qcfg['voting'][str(interaction.guild_id)] is True:
            qmsg = await interaction.original_response()

            newkarma = await karma_helper(interaction, qmsg, qid, karma)
            karmadiff = newkarma[1] - karma
            
            try:
                quoteview.set_footer(text=f"Score: {'+' if newkarma[1] > 0 else ''}{newkarma[1]} ({'went up by +{karmadiff} pts'.format(karmadiff=karmadiff) if karmadiff > 0 else 'went down by {karmadiff} pts'.format(karmadiff=karmadiff) if karmadiff < 0 else 'did not change'} this time).")
                update_karma(qid,newkarma[1])
                logger.info(f"Quote {qid} karma updated to {newkarma[1]} in guild {interaction.guild_id}")
                await qmsg.edit(embed=quoteview)
                await qmsg.clear_reactions()
            except Exception as error:
                quoteview.set_footer(text=f"Score: {'+' if karma > 0 else ''}{karma} (no change due to error: {error}")
                logger.error(f"Error updating karma for quote {qid} in guild {interaction.guild_id}: {error}")
                await qmsg.edit(embed=quoteview)

    @app_commands.command(name="add")
    @app_commands.describe(author='User who said the quote',content='The quote itself',time='When the quote happened',source='URL where the quote came from, if applicable')
    async def quote_addbyhand(self, interaction: discord.Interaction, author: discord.Member, content: str, time: str=datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f %z"), source: str = None):
        """Create a quote manually, eg. for things said in VOIP"""

        deferpost = await interaction.response.defer(thinking=True,)
        newpost = await interaction.original_response()

        try:
            
            # Parse entered timestamp
            timestamp = parse(time, default=datetime.now())
            
            sql_values = (
                content,
                author.id,
                author.name,
                interaction.user.id,
                interaction.guild_id if bool(interaction.guild_id) else interaction.channel.id,
                None,
                int(datetime.timestamp(timestamp)),
                source if validators.url(source) else None
            )
            
            qid,karma = insert_quote(sql_values)

            logger.info("Quote saved successfully")
            logger.debug(format_quote(content, authorName=author.name, timestamp=int(datetime.timestamp(timestamp))))
            
            quote = format_quote(content, authorID=author.id, authorName=author.name, timestamp=int(datetime.timestamp(timestamp)), format='discord_embed')
            quote.add_field(name='Status',value=f'Quote saved successfully.')
            if bool(source) and not validators.url(source):
                quote.add_field(name='Note',value=f'Value "{source}" for Source was not an URL and was therefore ignored.',inline=False)
            authorAvatar = author.display_avatar
            quote.set_thumbnail(url=authorAvatar.url)

            if qcfg['voting']['enable'] is True and interaction.is_guild_integration():
              if str(interaction.guild_id) in qcfg['voting'] and qcfg['voting'][str(interaction.guild_id)] is True:
                quote.set_footer(text=f"Score: {'+' if karma > 0 else ''}{karma}. Voting is open for {qvote_timeout} minutes.")
              else:
                logger.warning(f"Did not enable voting for quote {qid} in guild {interaction.guild_id}. Guild voting: {qcfg['voting'][str(interaction.guild_id)] if str(interaction.guild_id) in qcfg['voting'] else 'not set'}")
            else:
                logger.warning(f"Did not enable voting for quote {qid} in guild {interaction.guild_id}. Global Voting: {qcfg['voting']['enable']}. Running as guild integration: {interaction.is_guild_integration()}")
            # Send the resulting quote
            await newpost.edit(allowed_mentions=discord.AllowedMentions.none(),embed=quote)
            logger.info(f"Quote {qid} requested by {interaction.user} ({interaction.user.id}) in guild {interaction.guild_id} ({interaction.guild.name if interaction.guild else 'DM'})")
            
            if qcfg['voting']['enable'] is True and interaction.is_guild_integration():
              if str(interaction.guild_id) in qcfg['voting'] and qcfg['voting'][str(interaction.guild_id)] is True:
                qmsg = await interaction.original_response()

                newkarma = await karma_helper(interaction, qmsg, qid, karma)
                karmadiff = newkarma[1] - karma
                
                try:
                    quote.set_footer(text=f"Score: {'+' if newkarma[1] > 0 else ''}{newkarma[1]} ({'went up by +{karmadiff} pts'.format(karmadiff=karmadiff) if karmadiff > 0 else 'went down by {karmadiff} pts'.format(karmadiff=karmadiff) if karmadiff < 0 else 'did not change'} this time).")
                    update_karma(qid,newkarma[1])
                    logger.info(f"Quote {qid} karma updated to {newkarma[1]} in guild {interaction.guild_id}")
                    await qmsg.edit(embed=quote)
                    await qmsg.clear_reactions()
                except Exception as error:
                    quote.set_footer(text=f"Score: {'+' if karma > 0 else ''}{karma} (no change due to error: {error}")
                    logger.error(f"Error updating karma for quote {qid} in guild {interaction.guild_id}: {error}")
                    await qmsg.edit(embed=quote)
            
            con.close()
        except psycopg2.DatabaseError as error:
            await interaction.response.send_message(f'Error: SQL Failed due to:\n```{str(error.with_traceback)}```',ephemeral=True)
            logger.error("QUOTE SQL ERROR:\n" + str(error.with_traceback))
        except dateutil.parser._parser.ParserError as error:
            await interaction.response.send_message(f'Error: {error}',ephemeral=True)

@app_commands.context_menu(name='Save as quote!')
async def quote_save(interaction: discord.Interaction, message: discord.Message):
    
    deferpost = await interaction.response.defer(thinking=True,)
    newpost = await interaction.original_response()

    try:
        con = psycopg2.connect(
        database=sqlcfg['database'],
        user=sqlcfg['user'],
        password=sqlcfg['password'] if 'password' in sqlcfg else None,
        host=sqlcfg['host'],
        port=sqlcfg['port']
    )
        cur = con.cursor()

        # Strip any mention from the beginning of the message
        strippedcontent = None
        if message.content.startswith('<@'):
            strippedcontent = re.sub(r'^\s*<@!?[0-9]+>\s*', '', message.content)

        # Check for duplicates first
        cur.execute("SELECT 1 from sanford.quotes WHERE msgID='" + str(message.id) + "'")
        if cur.fetchone() is not None:
            raise LookupError('This quote is already in the database.')
        con.close()

        sql_values = (
            strippedcontent if bool(strippedcontent) else message.content,
            message.author.id,
            message.author.name,
            interaction.user.id,
            interaction.guild_id if bool(interaction.guild_id) else interaction.channel.id,
            message.id,
            int(datetime.timestamp(message.created_at)),
            message.jump_url
            )

        qid,karma = insert_quote(sql_values)
        if karma == None: karma = 1

        quote = format_quote(message.content, authorID=message.author.id, timestamp=int(message.created_at.timestamp()), format='discord_embed')
        quote.add_field(name='Status',value=f'[Quote]({message.jump_url}) saved successfully.')
        
        authorAvatar = message.author.display_avatar
        quote.set_thumbnail(url=authorAvatar.url)

        logger.info("Quote saved successfully")
        logger.debug(format_quote(message.content, authorName=message.author.name, timestamp=int(message.created_at.timestamp())),)
        
        if qcfg['voting']['enable'] is True and interaction.is_guild_integration():
          if str(interaction.guild_id) in qcfg['voting'] and qcfg['voting'][str(interaction.guild_id)] is True:
            quote.set_footer(text=f"Score: {'+' if karma > 0 else ''}{karma}. Voting is open for {qvote_timeout} minutes.")
          else:
            logger.warning(f"Did not enable voting for quote {qid} in guild {interaction.guild_id}. Guild voting: {qcfg['voting'][str(interaction.guild_id)] if str(interaction.guild_id) in qcfg['voting'] else 'not set'}")
        else:
            logger.warning(f"Did not enable voting for quote {qid} in guild {interaction.guild_id}. Global Voting: {qcfg['voting']['enable']}")
        # Send the resulting quote
        await newpost.edit(allowed_mentions=discord.AllowedMentions.none(),embed=quote)
        logger.info(f"Quote {qid} requested by {interaction.user} ({interaction.user.id}) in guild {interaction.guild_id} ({interaction.guild.name if interaction.guild else 'DM'})")
        
        if qcfg['voting']['enable'] is True and interaction.is_guild_integration():
          if str(interaction.guild_id) in qcfg['voting'] and qcfg['voting'][str(interaction.guild_id)] is True:
            qmsg = await interaction.original_response()

            newkarma = await karma_helper(interaction, qmsg, qid, karma)
            karmadiff = newkarma[1] - karma
            
            try:
                quote.set_footer(text=f"Score: {'+' if newkarma[1] > 0 else ''}{newkarma[1]} ({'went up by +{karmadiff} pts'.format(karmadiff=karmadiff) if karmadiff > 0 else 'went down by {karmadiff} pts'.format(karmadiff=karmadiff) if karmadiff < 0 else 'did not change'} this time).")
                update_karma(qid,newkarma[1])
                logger.info(f"Quote {qid} karma updated to {newkarma[1]} in guild {interaction.guild_id}")
                await qmsg.edit(embed=quote)
                await qmsg.clear_reactions()
            except Exception as error:
                quote.set_footer(text=f"Score: {'+' if karma > 0 else ''}{karma} (no change due to error: {error}")
                logger.error(f"Error updating karma for quote {qid} in guild {interaction.guild_id}: {error}")
                await qmsg.edit(embed=quote)
        
        con.close()
    except psycopg2.DatabaseError as error:
        await interaction.response.send_message(f'Error: SQL Failed due to:\n```{str(error.with_traceback)}```',ephemeral=True)
        logger.error("QUOTE SQL ERROR:\n" + str(error.with_traceback))
    except dateutil.parser._parser.ParserError as error:
        await interaction.response.send_message(f'Error: {error}',ephemeral=True)

    @commands.Cog.listener()
    async def on_ready(self):
        pass

async def setup(bot):
    logger.info("Loading Quotes cog extension.")
    await bot.add_cog(Quotes(bot))
    bot.tree.add_command(quote_save)

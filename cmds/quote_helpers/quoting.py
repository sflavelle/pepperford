from datetime import datetime
import psycopg2
import discord
import yaml
import re
import asyncio
import logging

with open('config.yaml', 'r') as file:
    cfg = yaml.safe_load(file)

# setup logging
logger = logging.getLogger('discord.quotes.helpers')

sqlcfg = cfg['bot']['psql']

def format_quote(content,timestamp,authorID=None,authorName=None,bot=None,source=None,format: str='plain'):
    quote_string_id = '''"{0}"
    —<@{1}> / {2}'''
    quote_string_name = '''"{0}"
    —{1} / {2}'''
    mdquote_string_id = '''"{0}"
    —<@{1}> / [{2}]({3})''' if source is not None else '''"{0}"
    —<@{1}> / {2}'''
    mdquote_string_name = '''"{0}"
    *—{1} / [{2}]({3})*''' if source is not None else '''"{0}"
    *—{1} / {2}*'''

    # Adjust timestamp if milliseconds included
    # For parsing later
    if len(str(timestamp)) > 10:
        timestamp = timestamp / (10 ** (len(str(timestamp)) - 10))

    # No timestamp? Fudge one
    if bool(timestamp) == False:
        dateprint = "Octember 32, " + str(datetime.today().year)
    else:
    # Else generate a date string
        date = datetime.fromtimestamp(timestamp)
        if format == "discord_embed" or format == "markdown":
            dateprint = f"<t:{int(timestamp)}:D>"
        else:
            dateprint = date.strftime("%B %d, %Y")

    # Choose string based on provided info
    match format:
        case 'plain':            
            if bool(authorID):
                return quote_string_id.format(content,authorID,dateprint)
            elif bool(authorName) and not bool(authorID):
                return quote_string_name.format(content,authorName,dateprint)
        case 'markdown':
            if bool(authorID):
                return mdquote_string_id.format(content,authorID,dateprint,source)
            elif bool(authorName) and not bool(authorID):
                return mdquote_string_name.format(content,authorName,dateprint,source)
        case 'discord_embed':
            
            if bool(authorID):
                embed = discord.Embed(
                    description=mdquote_string_id.format(content,authorID,dateprint,source)
                )
                return embed
            elif bool(authorName) and not bool(authorID):
                embed = discord.Embed(
                    description=mdquote_string_name.format(content,authorName,dateprint,source)
                )
                return embed

### SQL FUNCTIONS

def random_quote(gid: int = None,uid: int = None,sort_order: str = "random()"):
    con = psycopg2.connect(
        database=sqlcfg['database'],
        user=sqlcfg['user'],
        password=sqlcfg['password'] if 'password' in sqlcfg else None,
        host=sqlcfg['host'],
        port=sqlcfg['port'],
)
    cur = con.cursor()
    
    where_filter = []
    if bool(gid): where_filter.append(f"guild='{str(gid)}'")
    if bool(uid): where_filter.append(f"authorid {('= ' + str(uid) + '') if type(uid) == int else ('= ' + str(uid[0]) + '') if len(uid) == 1 else ('=ANY ' +  str(uid))}")
#    if bool(exclude_list): where_filter.append(f"authorid NOT IN ({str(exclude_list).strip('[]')})")

    query = f"SELECT id,content,authorid,authorname,timestamp,karma,source FROM sanford.quotes {'WHERE ' + ' AND '.join(where_filter) if bool(uid) or bool(gid) else ''} ORDER BY {sort_order} LIMIT 1"
    logger.debug(query)
    # Fetch a random quote from the SQL database
    try:
        cur.execute(query)
        id,content,aID,aName,timestamp,karma,source = cur.fetchone()
    except TypeError as error:
        if bool(uid) and "NoneType object" in str(error):
            raise LookupError("Sorry, that user doesn't have any quotes saved in this server yet!")
    cur.close()
    con.close()
    return (id, content, aID, aName, timestamp, karma, source)

def insert_quote(quote_data: tuple):
    con = psycopg2.connect(
        database=sqlcfg['database'],
        user=sqlcfg['user'],
        password=sqlcfg['password'] if 'password' in sqlcfg else None,
        host=sqlcfg['host'],
        port=sqlcfg['port']
    )
    cur = con.cursor()
    
    # Validate quote tuple first
    if len(quote_data) != 8:
        raise Exception(f"Quote object has {len(quote_data)} items (should be 8)")
    
    
    cur.execute("INSERT INTO sanford.quotes (content, authorid, authorname, addedby, guild, msgid, timestamp, source) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id, karma;", quote_data)
    returning = cur.fetchone()
    con.commit()
    cur.close()
    con.close()
    return returning

def update_karma(qid,karma):
    con = psycopg2.connect(
        database=sqlcfg['database'],
        user=sqlcfg['user'],
        password=sqlcfg['password'] if 'password' in sqlcfg else None,
        host=sqlcfg['host'],
        port=sqlcfg['port']
    )
    cur = con.cursor()
    cur.execute("UPDATE sanford.quotes SET karma= %s WHERE id= %s", (karma, qid))
    con.commit()
    cur.close()
    con.close()


    
async def karma_helper(interaction: discord.Interaction, qid, karma):
    # config vars
    timeout = cfg['sanford']['quoting']['vote_timeout']
    # Let's allow the quote to be voted on
    thumbsUp, thumbsDown = "👍", "👎"
    
    imsg = await interaction.original_response()
    msg = await imsg.fetch()
    
    await msg.add_reaction(thumbsUp)
    await msg.add_reaction(thumbsDown)
    
    
    await asyncio.sleep(60*timeout) # Wait 3 minutes before collecting reactions
    
    msg = await msg.fetch()
    
    upCount = 0
    downCount = 0
    
    # Count the reactions (Sanfords doesn't count)
    for e in msg.reactions:
        match e.emoji:
            case "👍":
                upCount = e.count - 1
            case "👎":
                downCount = e.count - 1
                
    karmadiff = 0 + upCount - downCount
    newkarma = karma + karmadiff
    
    return (qid, newkarma)
    
### MASTOPOSTER-CENTRIC FUNCTIONS
    
def rename_user(id, fallback: str):
    for k,v in cfg['mappings']['users'].items():
        if k == id:
            # Found our author!
            return v
    else:
        return fallback
    
def strip_discord_format(str):
    emoji = re.compile(r"<(:\S+:)\d+>")
    user = re.compile(r"<@!?(\d+)>")
    # replace emoji mentions with just the :emoji: string
    str = re.sub(emoji,r"\g<1>",str)
    
    usermatches = user.finditer(str)
    
    for match in usermatches:
        str = str.replace(f"<@{match.group(1)}>", rename_user(match.group(1), '(user id here, no match)'))
        str = str.replace(f"<@!{match.group(1)}>", rename_user(match.group(1), '(user id here, no match)'))
        
    return str
            
    

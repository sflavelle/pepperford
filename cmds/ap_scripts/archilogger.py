import datetime
import json
import logging
import os
import pickle
import sys

import regex as re
import time
import typing
import functools
from multiprocessing import Process
from collections import defaultdict
# from paho.mqtt import client as mqtt_client

import discord
import requests
import validators
import yaml
from discord import HTTPException
from discord.ext import tasks, commands

from ap_itemlog import room_id
from cmds.ap_scripts.utils import Player, Item, CollectedItem, handle_item_tracking, handle_location_tracking

logger = logging.getLogger('archilogger')

with open('config.yaml', 'r', encoding='UTF-8') as file:
    cfg = yaml.safe_load(file)

INTERVAL = 20

# small functions
to_epoch = lambda timestamp : time.mktime(datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f").timetuple())


def dimIfGoaled(player: Player) -> str:
    if player.is_finished():
        return "-# "
    else: return ""


class ItemLog:

    bot = None

    guild: discord.Guild
    host: str

    ap_version: str
    room_id: str
    seed_id: str = None

    world_settings = {}
    players: dict[str, Player]
    spoiler_log = {}

    running: bool = False

    log: 'LogInput'
    out: 'LogOutput'

    class LogInput(dict):
        """Container class for receiving text from an Archipelago log."""
        cookie: str = None

        log_url: str = None
        log = None
        last_line: int = 0

        def __init__(self, cookie, log_url: str):
            super().__init__()
            self.cookie = cookie
            self.log_url = log_url

            self.room_id = self.log_url.split('/')[-1]
            self.host = self.log_url.split('/')[2]

            if os.path.exists(f"itemlog-{self.room_id}-log.pickle"):
                with open(f"itemlog-{self.room_id}-log.pickle", 'rb') as f:
                    self.log = pickle.load(f)

        def fetch(self) -> bool:
            try:
                cookies = {'session': self.cookie}
                response = requests.get(self.log_url, cookies=cookies, timeout=5)
                response.raise_for_status()
                self.log = response.text.splitlines()
                with open(f"itemlog-{self.room_id}-log.pickle", 'rb') as f:
                    pickle.dump(self.log,f,pickle.HIGHEST_PROTOCOL)
                return True
            except requests.RequestException as e:
                logger.error(f"Error fetching log file: {e}")
                return False


    class LogOutput(dict):
        """Container class for sending text out."""
        _bot: discord.Client
        log_channel: discord.TextChannel|discord.Thread
        chat_channel: discord.TextChannel|discord.Thread
        send_chat: bool = True
        send_mqtt: bool = True
        mqtt_details: tuple[str, int, str, str] = (None, 1883, 'mqtt', 'mqttpassword')
        mqtt_client = None

        MSG_MAXCHARS = 2000

        filter: 'ItemFilter'

        message_buffer: list[str] = []
        release_buffer = {}

        def __init__(self, log_channel: discord.TextChannel | discord.Thread,
                     chat_channel: discord.TextChannel | discord.Thread,
                     mqtt_details: tuple[str, int, str, str] = None):
            super().__init__()
            self.log_channel = log_channel
            self.chat_channel = chat_channel
            self.filter = self.ItemFilter()
            self.mqtt_details = mqtt_details
            self.mqtt_base = f"{cfg['mqtt']['topic_base']}/{room_id}"
            if bool(self.mqtt_details):
                self.send_mqtt = True
                logger.info("MQTT details entered, starting MQTT c")
                self.mqtt_client = mqtt_client.Client(cfg['mqtt']['topic_base'])
                self.mqtt_client.username_pw_set(self.mqtt_details[2],self.mqtt_details[3])
                self.mqtt_client.connect_async(self.mqtt_details[0], self.mqtt_details[1])
                self.mqtt_client.loop_start()


        def getChatEnabled(self) -> bool:
            return bool(self.chat_channel) and self.send_chat

        def setFilter(self,p,u,c,f,t):
            self.filter = self.ItemFilter(p,u,c,f,t)

        def getMqttEnabled(self) -> bool:
            return self.send_mqtt and self.mqtt_details and isinstance(self.mqtt_client, mqtt_client.Client)

        async def send(self, channel: discord.TextChannel|discord.Thread, msg: str, chat_user: str = None) -> bool:
            try:
                msgObject = await channel.send(content=msg,
                             # username=chat_user,
                             )

                if bool(msgObject):
                    return True
            except HTTPException as error:
                if len(msg) > self.MSG_MAXCHARS:
                    if len(msg) > (self.MSG_MAXCHARS * 8): # way too long
                        raise ValueError
                    # Message too long, let's try splitting it up
                    # later
                    logger.error("Tried to send a message that was too long.")

                else: logger.error(f"Failed to send a webhook message (HTTPException): {error}.")
            except discord.errors.NotFound as error:
                logger.error("The configured webhook doesn't exist?")
            except BaseException as error:
                logger.error(f"Failed to send webhook: {error}")
            finally:
                return False

        async def sendmqtt(self, topic: str, message: str) -> bool:
            if not self.getMqttEnabled():
                return False

            fulltopic = f"{cfg['mqtt']['topic_base']}/{topic}"
            result = self.mqtt_client.publish(fulltopic, message)
            if result[0]:
                logger.info(f"Sent MQTT @ {fulltopic}: {message}")
                return True
            else:
                logger.error(f"Failed to send MQTT @ {fulltopic}")
                return False

        class ItemFilter:
            """Controls whether an item of a given classification should be posted or skipped over."""
            progression: bool = True
            useful: bool = True
            currency: bool = False
            filler: bool = False
            trap: bool = True

            def __init__(self, prog: bool = None, use: bool = None, money: bool = None, filler: bool = None, trap: bool = None, unclassified: bool = True):
                self.progression = prog
                self.useful = use
                self.currency = money
                self.filler = filler
                self.trap = trap
                self.unclassified = unclassified # defaults to true, this may change in future

            def get(self, classification: str) -> bool:
                if classification is None: return self.unclassified
                return getattr(self, classification)


    def __init__(self, bot, log_url: str,
                 log_channel: discord.TextChannel | discord.Thread,
                 cookie: str = cfg['bot']['archipelago']['session_cookie'], seed_url: str = None,
                 chat_channel: discord.TextChannel | discord.Thread = None) -> None:

        super().__init__()

        self.bot = bot
        self.guild = guild
        self.cookie = cookie

        for url in [log_url, seed_url]:
            if url is None: continue
            if not validators.url(url):
                raise ValueError(f"URL did not validate: {url}")

        self.log = self.LogInput(cfg['bot']['archipelago']['session_cookie'], log_url)
        self.out = self.LogOutput(log_channel, chat_channel)

        self.room_id = log_url.split('/')[-1]
        self.host = log_url.split('/')[2]
        self.seed_id = seed_url.split('/')[-1] if bool(seed_url) else None

        self.players = {}

        if os.path.exists(f"itemlog-{self.room_id}-log.pickle"):
            with open(f"itemlog-{self.room_id}-log.pickle", 'rb') as f:
                state = pickle.load(f)
                if len(self.log.log) > 1:
                    self.log.last_line = state['last_line']
            logger.info("Loaded itemlog state from file cache.")

        api_url = f"https://{self.host}/api/room_status/{self.room_id}"

        logger.info("Fetching room info.")
        for player in requests.get(api_url).json()["players"]:
            self.players[player[0]] = Player(
                name=player[0],
                game=player[1]
            )
            if not self.has_spoiler():
                self.spoiler_log[player[0]] = {}

    def has_spoiler(self) -> tuple[bool, bool]:
        """Returns two bools.
        The first is True if the seed URL (for retrieving the spoiler log) was provided.
        The second is True if the spoiler log has been parsed."""
        return bool(self.seed_id), len(self.spoiler_log) > 0

    def parse_spoiler_log(self) -> bool:
        """From the provided host and seed ID, retrieves the spoiler log.
        Stores player settings in each Player object.
        Stores each item/location in spoiler_log."""

        if not bool(self.seed_id):
            logger.error("Itemlog has no seed URL provided, cannot parse spoiler.")
            return False

        logger.info("Processing spoiler log.")

        spoiler_url = f"https://{self.host}/dl_spoiler/{self.seed_id}"

        spoiler_log = requests.get(spoiler_url, timeout=10)
        spoiler_text = spoiler_log.text.split('\n')

        parse_mode = "Seed Info"
        working_player = None

        regex_patterns = {
            'location': re.compile(r'(.+) \((.+?)\): (.+) \((.+?)\)$'),
            'starting_item': re.compile(r'^(.+) \((.+?)\)$')
        }

        def parse_to_type(value):
            constructors = [int, str]
            if value == '': return None
            if value.lower() in ['yes', 'true']: return True
            elif value.lower() in ['no', 'false']: return False
            for c in constructors:
                try:
                    return c(value)
                except ValueError:
                    pass

        for line in spoiler_text:
            line = str(line)
            if len(line) == 0:
                continue

            if line.startswith("Archipelago Version"):
                parse_mode = "Seed Info"
            if line.startswith("Player "):
                parse_mode = "Players"
                working_player = line.strip().split(':', 1)[1].strip()
                logger.info(f"Parsing settings for player {working_player}")
            if line == "Locations:":
                parse_mode = "Locations"
                logger.info("Parsing multiworld locations")
                continue
            if line == "Starting Items:":
                parse_mode = "Starting Items"
                logger.info("Parsing starting items")
            if line in ["Entrances:","Medallions:","Fairy Fountain Bottle Fill:", "Shops:"]:
                parse_mode = None

            match parse_mode:
                case "Seed Info":
                    try:
                        if line.startswith("Archipelago"):
                            self.ap_version = line.split(' ')[2]
                            logger.info(f"Parsing seed {self.seed_id}")
                            logger.info(f"Generated on Archipelago version {self.ap_version}")
                        else:
                            current_key, value = line.strip().split(':', 1)
                            self.world_settings[current_key.strip()] = parse_to_type(value.lstrip())
                    except ValueError as error:
                        logger.error(f"Couldn't parse line: {error}")
                        logger.error(line)

                case "Players":
                    current_key, value = line.strip().split(':', 1)
                    if current_key.strip() == "Game":
                        if working_player not in self.players:
                            self.players[working_player] = Player(working_player,value.lstrip())
                    if value.lstrip().startswith("[") or value.lstrip().startswith("{"):
                        try:
                            self.players[working_player].settings[current_key.strip()] = json.loads(value.lstrip())
                        except ValueError:
                            pass
                    else:
                        self.players[working_player].settings[current_key.strip()] = parse_to_type(value.lstrip())
                case "Locations":
                    if match := regex_patterns['location'].match(line):
                        item_location, sender, item, receiver = match.groups()
                        item_location = item_location.lstrip()
                        if sender == receiver and (item_location == item or item in item_location or item_location in item):
                            continue # Most likely an event, can be skipped
                        ItemObject = Item(self.players[sender],self.players[receiver],item,item_location)
                        if sender not in self.spoiler_log: self.spoiler_log.update({sender: {}})
                        self.spoiler_log[sender].update({item_location: ItemObject})
                case "Starting Items":
                    if match := regex_patterns['starting_item'].match(line):
                        item, receiver = match.groups()
                        ItemObject = CollectedItem("Archipelago",self.players[receiver],item,"Starting Items")
                        self.players[receiver].items[item] = ItemObject
                case _:
                    continue
        logger.info("Done parsing the spoiler log")
        return True

    def process_new_log_lines(self, new_lines, skip_msg: bool = False):
        """For each line in, parse its message type and act accordingly.
        Set skip_msg True to prevent messages from being sent to the message/release buffer."""

        release_buffer = self.out.release_buffer

        # Regular expressions for different log message types
        regex_patterns = {
            'sent_items': re.compile(r'\[(.*?)]: \(Team #\d\) (\L<players>) sent (.*?(?= to)) to (\L<players>) \((.+)\)$', players=self.players.keys()),
            'item_hints': re.compile(
                r'\[(.*?)]: Notice \(Team #\d\): \[Hint]: (\L<players>)\'s (.*) is at (.*) in (\L<players>)\'s World(?: at (?P<entrance>(.+)))?\.(?<! \(found\))$', players=self.players.keys()),
            'goals': re.compile(r'\[(.*?)]: Notice \(all\): (.*?) \(Team #\d\) has completed their goal\.$'),
            'releases': re.compile(
                r'\[(.*?)]: Notice \(all\): (.*?) \(Team #\d\) has released all remaining items from their world\.$'),
            'messages': re.compile(r'\[(.*?)]: Notice \(all\): (.*?): (.+)$'),
            'joins': re.compile(r'\[(.*?)]: Notice \(all\): (.*?) \(Team #\d\) playing (.+?) has joined. Client\(([0-9.]+)\), (?P<tags>.+)\.$'),
            'parts': re.compile(r'\[(.*?)]: Notice \(all\): (.*?) \(Team #\d\) has left the game\. Client\(([0-9.]+)\), (?P<tags>.+)\.$'),
        }

        for line in new_lines:
            if match := regex_patterns['sent_items'].match(line):
                timestamp, sender, item, receiver, item_location = match.groups()

                ReceivedItemObject = None

                # Mark item as collected
                try:
                    sender = self.players[sender]
                    receiver = self.players[receiver]

                    SentItemObject = Item(sender,receiver,item,item_location)
                    SentItemObject.found = True
                    self.spoiler_log[str(sender)].update({item_location: SentItemObject})
                    ReceivedItemObject = CollectedItem(sender,receiver,item,item_location)
                    if item in receiver.items: receiver.items[item].collect(sender, item_location)
                    else: receiver.items[item] = ReceivedItemObject
                    # players[sender].out(SentItemObject)
                    # self["spoiler"][sender]["locations"][item_location].collect()
                    if not skip_msg: logger.info(
                        f"{sender}: {item_location} -> {receiver}'s {item} ({ReceivedItemObject.classification})")

                    # By vote of spotzone: if it's filler, don't post it
                    if not self.out.filter.get(ReceivedItemObject.classification): continue
                except KeyError as e:
                    logger.error(f"""Sent Item Object Creation error. Parsed item name: '{item}', Receiver: '{receiver}', Location: '{item_location}', Error: '{str(e)}'""", e, exc_info=True)
                    logger.error(f"Line being parsed: {line}")

                # If this is part of a release, out it there instead
                if sender in self.out.release_buffer and not skip_msg and (to_epoch(timestamp) - self.out.release_buffer[sender]['timestamp'] <= 2):
                    self.out.release_buffer[sender]['items'][receiver].append(ReceivedItemObject)
                    logger.debug(f"Adding {item} for {receiver} to release buffer.")
                else:
                    # Update item name based on settings for special items
                    location = item_location
                    if bool(receiver.settings):
                        try:
                            item = handle_item_tracking(self, receiver, item)
                            location = handle_location_tracking(self, sender, item_location)
                        except KeyError as e:
                            logger.error(f"Couldn't do tracking for item {item} or location {location}:", e, exc_info=True)

                    # Update the message appropriately
                    if sender == receiver:
                        message = f"**{sender}** found **their own {
                            "hinted " if bool(self.spoiler_log[str(sender)][item_location].hinted) else ""
                            }{item}** ({location})"
                    elif bool(self.spoiler_log[str(sender)][item_location].hinted):
                        message = f"{dimIfGoaled(receiver)}{sender} found **{receiver}'s hinted {item}** ({location})"
                    else:
                        message = f"{dimIfGoaled(receiver)}{sender} sent **{item}** to **{receiver}** ({location})"
                    if not skip_msg: self.out.message_buffer.append(message.replace("_",r"\_"))


            elif match := regex_patterns['item_hints'].match(line):
                timestamp = match.groups()[0]
                receiver = match.groups()[1]
                item = match.groups()[2]
                item_location = match.groups()[3]
                sender = match.groups()[4]
                if match.group('entrance'):
                    entrance = match.group('entrance')
                else: entrance = None

                SentItemObject = Item(self.players[sender],self.players[receiver],item,item_location,entrance=entrance)
                if item_location not in self.spoiler_log[sender]:
                    self.spoiler_log[sender][item_location] = SentItemObject
                else: SentItemObject = self.spoiler_log[sender].get(item_location)
                message = f"**[Hint]** **{receiver}'s {item}** is at {item_location} in {sender}'s World{f" (found at {entrance})" if bool(entrance) else ''}."

                if not skip_msg and self.players[receiver].is_finished() is False and not SentItemObject.found: self.out.message_buffer.append(message)
                self.spoiler_log[sender][item_location].hint()


            elif match := regex_patterns['goals'].match(line):
                timestamp, sender = match.groups()
                self.players[sender].goaled = True
                message = f"**{sender} has finished!** That's {len([p for p in self.players.values() if p.is_goaled()])}/{len(self.players)} goaled! ({len([p for p in self.players.values() if p.is_finished()])}/{len(self.players)} including releases)"
                if not skip_msg: self.out.message_buffer.append(message)
            elif match := regex_patterns['releases'].match(line):
                timestamp, sender = match.groups()
                self.players[sender].released = True
                if not skip_msg:
                    logging.info("Release detected.")
                    self.out.release_buffer[sender] = {
                        'timestamp': to_epoch(timestamp),
                        'items': defaultdict(list)
                    }
            elif match := regex_patterns['messages'].match(line):
                timestamp, sender, message = match.groups()
                if self.out.getChatEnabled():
                    if message.startswith("!"): continue # don't out commands
                    else:
                        if not skip_msg and sender in self.players:
                            logger.info(f"{sender}: {message}")
                            self.out.send(self.out.chat_channel, sender, message)
            elif match := regex_patterns['joins'].match(line):
                timestamp, player, playergame, version, tags = match.groups()
                if not skip_msg: logger.info(f"{player} ({playergame}) is online.")
                self.players[player].set_online(True, timestamp)
            elif match := regex_patterns['parts'].match(line):
                timestamp, player, version, tags = match.groups()
                if not skip_msg: logger.info(f"{player} is offline.")
                self.players[player].set_online(False, timestamp)
            else:
                # Unmatched lines
                logger.debug(f"Unparsed line: {line}")

    @tasks.loop(minutes=1)
    async def main_loop(self, quiet: bool = False):
        """The main logging loop.
        Fetches the log, checks for updates, then processes those updates.
        Then, sends any queued up messages."""
        if self.log.fetch():
            if len(self.log.log) > self.log.last_line:
                new_lines = self.log.log[self.log.last_line:]
                logger.debug(f"new lines to process: {len(new_lines)}")
                self.process_new_log_lines(new_lines,quiet)

                self.log.last_line = len(self.log.log)

                logger.debug(f"message buffer length: {len(self.out.message_buffer)}")
                # Check message buffer, send any queued messages
                if len(self.out.message_buffer) > 0:
                    try:
                        await self.out.send(self.out.log_channel,"\n".join(self.out.message_buffer))
                        logger.info(f"sent {len(self.out.message_buffer)} messages to Discord")
                        self.out.message_buffer.clear()
                    except HTTPException as error:
                        logger.error(f"Error sending the messages out: {error}")
                    except ValueError as error:
                        # Message is 8x the character limit
                        # Intended to handle backlog messages
                        self.out.message_buffer.clear()

                else: logger.debug("No messages to send this loop.")
            with open(f"itemlog-{self.room_id}-log.pickle", 'wb') as f:
                pickle.dump({
                    "last_line": self.log.last_line
                }, f, pickle.HIGHEST_PROTOCOL)

if __name__ == "__main__":
    # URL of the log file and Discord webhook URL from environment variables
    log_url = os.getenv('LOG_URL')
    webhook_url = os.getenv('WEBHOOK_URL')
    session_cookie = os.getenv('SESSION_COOKIE')

    # Extra info for additional features
    seed_url = os.getenv('SPOILER_URL')
    msg_webhook = os.getenv('MSGHOOK_URL')

    if not (bool(log_url) or bool(webhook_url) or bool(session_cookie)):
        logger.error("Something required isn't configured properly!")
        sys.exit(1)

    itemlogger = ItemLog(

    )
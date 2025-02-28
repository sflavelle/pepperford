import datetime
import json
import time
import re
import os
import sys
import logging
from collections import defaultdict
import requests
import threading
from helpers_ap.ap_utils import Item, CollectedItem, Player, handle_item_tracking, handle_location_tracking
from word2number import w2n

# setup logging
logger = logging.getLogger('ap_itemlog')
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('[%(name)s %(process)d][%(levelname)s] %(message)s'))
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# Disclaimer: Copilot helped me with the initial setup of this file.
# Everything since is my own code. Thank you :-)

# URL of the log file and Discord webhook URL from environment variables
log_url = os.getenv('LOG_URL')
webhook_url = os.getenv('WEBHOOK_URL')
session_cookie = os.getenv('SESSION_COOKIE')

# Extra info for additional features
seed_url = os.getenv('SPOILER_URL')

if not (bool(log_url) or bool(webhook_url) or bool(session_cookie)):
    logger.error("Something required isn't configured properly!")
    sys.exit(1)

room_id = log_url.split('/')[-1]
hostname = log_url.split('/')[2]
seed_id = seed_url.split('/')[-1] if bool(seed_url) else None

api_url = f"https://{hostname}/api/room_status/{room_id}"

# Time interval between checks (in seconds)
INTERVAL = 30
# Maximum Discord message length in characters
MAX_MSG_LENGTH = 2000

# Buffer to store release and related sent item messages
release_buffer = {}
message_buffer = []

# Store for players, items, settings
players = {}
game = {
    'settings': {},
    'spoiler': {}
}

# small functions
goaled = lambda player : players[player].is_finished()
dim_if_goaled = lambda p : "-# " if goaled(p) else ""
to_epoch = lambda timestamp : time.mktime(datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f").timetuple())

def process_spoiler_log(seed_url):
    global players
    global game

    spoiler_url = f"https://{hostname}/dl_spoiler/{seed_id}"

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
            game['spoiler'][working_player] = {
                "items": {},
                "locations": {}
            }
        if line == "Locations:":
            parse_mode = "Locations"
            continue
        if line == "Starting Items:":
            parse_mode = "Starting Items"
        if line in ["Entrances:","Medallions:","Fairy Fountain Bottle Fill:", "Shops:"]:
            parse_mode = None

        match parse_mode:
            case "Seed Info":
                if line.startswith("Archipelago"):
                    game["settings"]["version"] = line.split(' ')[2]
                    game["settings"]["seed"] = parse_to_type(line.split(' ')[-1])
                else:
                    current_key, value = line.strip().split(':', 1)
                    game["settings"][current_key.strip()] = parse_to_type(value.lstrip())
            case "Players":
                current_key, value = line.strip().split(':', 1)
                if value.lstrip().startswith("[") or value.lstrip().startswith("{"): 
                    try:
                        players[working_player].settings[current_key.strip()] = json.loads(value.lstrip())
                    except ValueError:
                        pass
                else:
                    players[working_player].settings[current_key.strip()] = parse_to_type(value.lstrip())
            case "Locations":
                if match := regex_patterns['location'].match(line):
                    item_location, sender, item, receiver = match.groups()
                    if item_location == item and sender == receiver:
                        continue # Most likely an event, can be skipped
                    ItemObject = Item(sender,receiver,item,item_location,game=players[receiver].game)
                    if item_location not in game["spoiler"][sender]["locations"]:
                        game["spoiler"][sender]["locations"].update({item_location: ItemObject})
                        players[sender].locations.update({item_location: ItemObject})
                    if item not in game["spoiler"][receiver]["items"]:
                        ReceivedItemObject = CollectedItem(sender,receiver,item,item_location,game=players[receiver].game)
                        game["spoiler"][receiver]['items'].update({item: ReceivedItemObject})
            case "Starting Items":
                if match := regex_patterns['starting_item'].match(line):
                    item, receiver = match.groups()
                    ItemObject = CollectedItem("Archipelago",receiver,item,"Starting Items",game=players[receiver].game)
                    players[receiver].collect(ItemObject)
            case _:
                continue
    logger.info(f"Parsed seed {game['settings']['seed']}")
    logger.info(f"Generated on Archipelago version {game['settings']['version']}")

def process_new_log_lines(new_lines, skip_msg: bool = False):
    global release_buffer
    global players

    # Regular expressions for different log message types
    regex_patterns = {
        'sent_items': re.compile(r'\[(.*?)\]: \(Team #\d\) (.*?) sent (.*?) to (.{,16}?) \((.+)\)$'),
        'item_hints': re.compile(
            r'\[(.*?)\]: Notice \(Team #\d\): \[Hint\]: (.*?)\'s (.*) is at (.*) in (.*?)\'s World(?: at (?P<entrance>(.+)))?\.(?<! \(found\))$'),
        'goals': re.compile(r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has completed their goal\.$'),
        'releases': re.compile(
            r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has released all remaining items from their world\.$')
    }

    for line in new_lines:
        if match := regex_patterns['sent_items'].match(line):
            timestamp, sender, item, receiver, item_location = match.groups()

            # Mark item as collected 
            SentItemObject = Item(sender,receiver,item,item_location)
            game["spoiler"][sender]["locations"].update({item_location: SentItemObject})
            ReceivedItemObject = CollectedItem(sender,receiver,item,item_location)
            players[receiver].collect(ReceivedItemObject)
            players[sender].send(SentItemObject)
            game["spoiler"][sender]["locations"][item_location].collect()

            # By vote of spotzone: if it's filler, don't post it
            if ReceivedItemObject.is_filler() or ReceivedItemObject.is_currency(): continue

            # If this is part of a release, send it there instead
            if sender in release_buffer and not skip_msg and (to_epoch(timestamp) - release_buffer[sender]['timestamp'] <= 2):
                release_buffer[sender]['items'][receiver].append(ReceivedItemObject)
                logger.info(f"Adding {item} for {receiver} to release buffer.")
            else:
                # Update item name based on settings for special items
                location = item_location
                if bool(players[receiver].settings):
                    item = handle_item_tracking(players[receiver], item)
                    location = handle_location_tracking(players[sender], item_location)

                # Update the message appropriately
                if sender == receiver:
                    message = f"**{sender}** found **their own {
                        "hinted " if bool(game["spoiler"][sender]["locations"][item_location].hinted) else ""
                        }{item}** ({location})"
                elif bool(game["spoiler"][sender]["locations"][item_location].hinted):
                    message = f"{dim_if_goaled(receiver)}{sender} found **{receiver}'s hinted {item}** ({location})"
                else:
                    message = f"{dim_if_goaled(receiver)}{sender} sent **{item}** to **{receiver}** ({location})"
                if not skip_msg: message_buffer.append(message)


        elif match := regex_patterns['item_hints'].match(line):
            timestamp = match.groups()[0]
            receiver = match.groups()[1]
            item = match.groups()[2]
            item_location = match.groups()[3]
            sender = match.groups()[4]
            if match.group('entrance'):
                entrance = match.group('entrance')
            else: entrance = None

            SentItemObject = Item(sender,receiver,item,item_location,game=players[receiver].game,entrance=entrance)
            if item_location not in game["spoiler"][sender]["locations"]:
                game["spoiler"][sender]["locations"][item_location] = SentItemObject
            else: SentItemObject = game["spoiler"][sender]["locations"].get(item_location)
            message = f"**[Hint]** **{receiver}'s {item}** is at {item_location} in {sender}'s World{f" (found at {entrance})" if bool(entrance) else ''}."

            if not skip_msg and players[receiver].is_finished() is False and not SentItemObject.found: message_buffer.append(message)
            SentItemObject.hint()


        elif match := regex_patterns['goals'].match(line):
            timestamp, sender = match.groups()
            if sender not in players: players[sender] = {"goaled": True}
            players[sender].goaled = True
            message = f"**{sender} has finished!** That's {len([p for p in players.values() if p.is_goaled()])}/{len(players)} done!"
            if not skip_msg: message_buffer.append(message)
        elif match := regex_patterns['releases'].match(line):
            timestamp, sender = match.groups()
            players[sender].released = True
            if not skip_msg:
                logging.info("Release detected.")
                release_buffer[sender] = {
                    'timestamp': to_epoch(timestamp),
                    'items': defaultdict(list)
                }


def send_to_discord(message):
    payload = {
        "content": message
    }
    try:
        response = requests.post(webhook_url, json=payload, timeout=5)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Error sending message to Discord: {e}")


def send_release_messages():
    global release_buffer

    def handle_currency(receiver, itemlist: dict):
        currency = 0

        currency_matches = {
            'A Hat in Time': (re.compile(r'^([0-9]+) Pons$'), "Pons"),
            'Final Fantasy': (re.compile(r'^Gold([0-9]+)$'), "Gold"),
            'Jak and Daxter The Precursor Legacy': (re.compile(r'^([0-9]+) Precursor Orbs?$'), "Precursor Orbs"),
            "Links Awakening DX": (re.compile(r'^([0-9]+) Rupees$'), "Rupees"),
            'Link to the Past': (re.compile(r'^Rupees? \(([0-9]+)\)$'), "Rupees"),
            'Ocarina of Time': (re.compile(r'^Rupees? \(([0-9]+)\)$'), "Rupees"),
            'Pokemon FireRed and LeafGreen': (re.compile(r'^([0-9]+) Coins?$'), "Coins"),
            'Sonic Adventure 2 Battle': (re.compile(r'^(\w+) Coins?$'), "Coins"),
            'Super Mario World': (re.compile(r'^([0-9]+) coins?$'), "Coins"),
        }

        if players[receiver].game in currency_matches:
            try:
                for item, count in itemlist.copy().items():
                    if match := currency_matches[players[receiver].game][0].match(item):
                        if players[receiver].game == "Sonic Adventure 2 Battle":
                            amount = w2n.word_to_num(match.groups()[0]) # why you make me do this
                        else:
                            amount = int(match.groups()[0])
                        currency = currency + (amount * count)
                        del itemlist[item]
                if currency > 0:
                    logger.info(f"Replacing (attempting) currency in {players[receiver].game} with '{currency} {currency_matches[players[receiver].game][1]}'")
                    itemlist.update({f"{currency} {currency_matches[players[receiver].game][1]}": 1})
            except KeyError:
                logger.info(f"No currency handler for {players[receiver].game}, but handle_currency matched it anyway somehow!")
                raise

        return itemlist

    for sender, data in release_buffer.copy().items():
        if time.time() - data['timestamp'] > 1:
            message = f"**{sender}** has released their remaining items."
            running_message = message
            for receiver, items in data['items'].items():
                if players[receiver].is_finished():
                    continue
                item_counts = defaultdict(int)
                for item in items:
                    if item.is_not_important(): continue
                    item_counts[item] += 1
                handle_currency(receiver,item_counts)
                item_list = ', '.join(
                    [f"{item} (x{count})" if count > 1 else item for item, count in item_counts.items()])
                running_message += f"\n{dim_if_goaled(receiver)}**{receiver}** receives: {item_list}"
                if len(running_message) > MAX_MSG_LENGTH:
                    send_to_discord(message)
                    message = running_message.replace('\n','')
                else:
                    message = running_message
            send_to_discord(message)
            logger.info(f"{sender} release sent.")
            del release_buffer[sender]


def fetch_log(url):
    try:
        cookies = {'session': session_cookie}
        response = requests.get(url, cookies=cookies,timeout=5)
        response.raise_for_status()
        return response.text.splitlines()
    except requests.RequestException as e:
        logger.error(f"Error fetching log file: {e}")
        return []


def watch_log(url, interval):
    global release_buffer
    global players
    global game

    logger.info("Fetching room info.")
    for player in requests.get(api_url).json()["players"]:
        players[player[0]] = Player(
            name=player[0],
            game=player[1]
        )
        game['spoiler'][player[0]] = {
                "locations": {}
            }
    del player
    if seed_url:
        logger.info("Processing spoiler log.")
        process_spoiler_log(seed_url)
    previous_lines = fetch_log(url)
    process_new_log_lines(previous_lines, True) # Read for hints etc
    release_buffer = {}
    logger.info(f"Initial log lines: {len(previous_lines)}")
    # classification_thread = threading.Thread(target=save_classifications)
    # classification_thread.start()
    while True:
        time.sleep(interval)
        current_lines = fetch_log(url)
        if len(current_lines) > len(previous_lines):
            new_lines = current_lines[len(previous_lines):]
            process_new_log_lines(new_lines)
            if message_buffer:
                send_to_discord('\n'.join(message_buffer))
                logger.info(f"sent {len(message_buffer)} messages to webhook")
                message_buffer.clear()
            previous_lines = current_lines

def process_releases():
    global release_buffer
    logger.info("Watching for releases.")

    while True:
        time.sleep(10)
        while len(release_buffer) > 0:
            time.sleep(2)
            send_release_messages()

if __name__ == "__main__":
    logger.info(f"logging messages from AP Room ID {room_id} to webhook {webhook_url}")
    release_thread = threading.Thread(target=process_releases)
    release_thread.start()
    watch_log(log_url, INTERVAL)
import datetime
import json
import time
import re
import os
import sys
import logging
from collections import defaultdict
import requests

# setup logging
logger = logging.getLogger('ap_itemlog')
handler = logging.StreamHandler()
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
goaled = lambda player : players[player].goaled
dim_if_goaled = lambda p : "-# " if goaled(p) else ""
to_epoch = lambda timestamp : time.mktime(datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f").timetuple())

class Item:
    def __init__(self, sender, receiver, item, location):
        self.sender = sender
        self.receiver = receiver
        self.name = item
        self.location = location
        self.found = False
        self.hinted = False
        self.spoiled = False
    
    def __str__(self):
        return f"{self.receiver}'s {self.name}"

    def collect(self):
        self.found = True

    def hint(self):
        self.hinted = True

    def spoil(self):
        self.spoiled = True

class CollectedItem(Item):
    def __init__(self, sender, receiver, item, location):
        super().__init__(sender, receiver, item, location)
        self.count: int = 0

    def collect(self):
        self.found = True
        self.count = self.count + 1

class Player:
    def __init__(self,name,game):
        self.name = name
        self.game = game
        self.items = {}
        self.settings = PlayerSettings()
        self.goaled = False
    
    def collect(self, item: Item|CollectedItem):
        self.items.update({item.name: item})


class PlayerSettings(dict):
    def __init__(self):
        pass

def process_spoiler_log(seed_url):
    global players
    global game

    spoiler_url = f"https://{hostname}/dl_spoiler/{seed_id}"

    spoiler_log = requests.get(spoiler_url, timeout=10)
    spoiler_text = spoiler_log.text.split('\n')

    parse_mode = "Seed Info"
    working_player = None

    regex_patterns = {
        'location': re.compile(r'(.+) \((.+?)\): (.+) \((.+?)\)$')
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
        if line in ["Entrances:","Medallions:","Fairy Fountain Bottle Fill:", "Shops:", "Starting Items:"]:
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
                    if item_location not in game["spoiler"][sender]["locations"]:
                        SentItemObject = Item(sender,receiver,item,item_location)
                        game["spoiler"][sender]["locations"].update({item_location: SentItemObject})
                    if item not in game["spoiler"][receiver]["items"]:
                        ReceivedItemObject = CollectedItem(sender,receiver,item,item_location)
                        game["spoiler"][receiver]['items'].update({item: ReceivedItemObject})
            case _:
                continue
    logger.info(f"Parsed seed {game['settings']['seed']}")
    logger.info(f"Generated on Archipelago version {game['settings']['version']}")

def handle_item_tracking(item: str, player: str, game: str):
    if bool(players[player].settings):
        settings = players[player].settings
        match game:
            case "A Link to the Past":
                if item == "Triforce Piece" and "Triforce Hunt" in settings['Goal']:
                    required = settings['Triforce Pieces Required']
                    count = players[player].items[item].count
                    return f"{item} ({count}/{required})"
            case "A Hat in Time":
                if item == "Time Piece" and not settings['Death Wish Only']:
                    required = 0
                    match settings['End Goal']:
                        case 'Finale':
                            required = settings['Chapter 5 Cost']
                        case 'Rush Hour':
                            required = settings['Chapter 7 Cost']
                    count = players[player].items[item].count
                    return f"{item} ({count}/{required})"
            case "DOOM 1993":
                if item.endswith(" - Complete"):
                    count = len([i for i in players[player].items if i.endswith(" - Complete")])
                    required = 0
                    for episode in 1, 2, 3, 4:
                        if settings[f"Episode {episode}"] is True:
                            required = required + (1 if settings['Goal'] == "Complete Boss Levels" else 9)
                    return f"{item} ({count}/{required})"
            case "DOOM II":
                if item.endswith(" - Complete"):
                    count = len([i for i in players[player].items if i.endswith(" - Complete")])
                    required = 0
                    if settings["Episode 1"] is True:
                        required = required + 11 # MAP01-MAP11
                    if settings["Episode 2"] is True:
                        required = required + 9 # MAP12-MAP20
                    if settings["Episode 3"] is True:
                        required = required + 10 #  MAP21-MAP30
                    if settings["Secret Levels"] is True:
                        required = required + 2 # Wolfenstein/Grosse
                    return f"{item} ({count}/{required})"
            case "Here Comes Niko!":
                if item == "Cassette":
                    required = [max({k: v for k, v in settings.items() if "Cassette Cost" in k}.values())]
                    count = players[player].items[item].count
                    return f"{item} ({count}/{required})"
                if item == "Coin":
                    required = 76 if settings['Completion Goal'] == "Employee" else settings['Elevator Cost']
                    count = players[player].items[item].count
                    return f"{item} ({count}/{required})"
                if item in ["Hairball City Fish", "Turbine Town Fish", "Salmon Creek Forest Fish", "Public Pool Fish", "Bathhouse Fish", "Tadpole HQ Fish"] and settings['Fishsanity'] == "Insanity":
                    required = 5
                    count = players[player].items[item].count
                    return f"{item} ({count}/{required})"
            case "Ocarina of Time":
                if item == "Triforce Piece" and settings['Triforce Hunt'] is True:
                    required = settings['Required Triforce Pieces']
                    count = players[player].items[item].count
                    return f"{item} ({count}/{required})"
                if item == "Gold Skulltula Token":
                    required = 50
                    count = players[player].items[item].count
                    return f"{item} ({count}/{required})"
            case "Sonic Adventure 2 Battle":
                if item == "Emblem":
                    required = round(settings['Max Emblem Cap'] * (settings["Emblem Percentage for Cannon's Core"] / 100))
                    count = players[player].items[item].count
                    return f"{item} ({count}/{required})"
            case "Super Mario World":
                if item == "Yoshi Egg" and settings['Goal'] == "Yoshi Egg Hunt":
                    count = players[player].items[item].count
                    required = round(
                        settings['Max Number of Yoshi Eggs']
                        * (settings['Required Percentage of Yoshi Eggs'] / 100))
                    return f"{item} ({count}/{required})"
            case _:
                return item
    
    # Return the same name if nothing matched (or no settings available)
    return item

def process_new_log_lines(new_lines, skip_msg: bool = False):
    global release_buffer
    global players

    # Regular expressions for different log message types
    regex_patterns = {
        'sent_items': re.compile(r'\[(.*?)\]: \(Team #\d\) (.*?) sent (.*?) to (.{,16}?) \((.+)\)$'),
        'item_hints': re.compile(
            r'\[(.*?)\]: Notice \(Team #\d\): \[Hint\]: (.*?)\'s (.*) is at (.*) in (.*?)\'s World\.(?<! \(found\))$'),
        'goals': re.compile(r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has completed their goal\.$'),
        'releases': re.compile(
            r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has released all remaining items from their world\.$')
    }

    for line in new_lines:
        if match := regex_patterns['sent_items'].match(line):
            timestamp, sender, item, receiver, item_location = match.groups()

            # Mark item as collected 
            if item_location not in game["spoiler"][sender]["locations"]:
                SentItemObject = Item(sender,receiver,item,item_location)
                game["spoiler"][sender]["locations"].update({item_location: SentItemObject})
            if item not in players[receiver].items:
                ReceivedItemObject = CollectedItem(sender,receiver,item,item_location)
                players[receiver].collect(ReceivedItemObject)
                players[receiver].items[item].collect()
            else: players[receiver].items[item].collect()
            game["spoiler"][sender]["locations"][item_location].collect()

            # If this is part of a release, send it there instead
            if sender in release_buffer and not skip_msg and (to_epoch(timestamp) - release_buffer[sender]['timestamp'] <= 2):
                release_buffer[sender]['items'][receiver].append(item)
                logger.info(f"Adding {item} for {receiver} to release buffer.")
            else:
                # Update item name based on settings for special items
                if bool(players[receiver].settings):
                    item = handle_item_tracking(item, receiver, players[receiver].game)

                # Update the message appropriately
                if sender == receiver:
                    message = f"**{sender}** found their own {"hinted " if bool(game["spoiler"][sender]["locations"][item_location].hinted) else ""}**{item}** ({item_location})"
                elif bool(game["spoiler"][sender]["locations"][item_location].hinted):
                    message = f"{dim_if_goaled(receiver)}{sender} found **{receiver}'s hinted {item}** ({item_location})"
                else:
                    message = f"{dim_if_goaled(receiver)}{sender} sent **{item}** to **{receiver}** ({item_location})"
                if not skip_msg: message_buffer.append(message)


        elif match := regex_patterns['item_hints'].match(line):
            timestamp, receiver, item, item_location, sender = match.groups()
            SentItemObject = Item(sender,receiver,item,item_location)
            if item_location not in game["spoiler"][sender]["locations"]:
                game["spoiler"][sender]["locations"][item_location] = SentItemObject
            message = f"**[Hint]** **{receiver}'s {item}** is at {item_location} in {sender}'s World."

            if not skip_msg and players[receiver].goaled is False and not SentItemObject.hinted and not SentItemObject.found: message_buffer.append(message)
            SentItemObject.hint()


        elif match := regex_patterns['goals'].match(line):
            timestamp, sender = match.groups()
            if sender not in players: players[sender] = {"goaled": True}
            message = f"**{sender} has finished!**"
            players[sender].goaled = True
            if not skip_msg: message_buffer.append(message)
        elif match := regex_patterns['releases'].match(line):
            timestamp, sender = match.groups()
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


    for sender, data in release_buffer.copy().items():
        if time.time() - data['timestamp'] > INTERVAL:
            message = f"**{sender}** has released their remaining items."
            for receiver, items in data['items'].items():
                item_counts = defaultdict(int)
                for item in items:
                    item_counts[item] += 1
                item_list = ', '.join(
                    [f"{item} (x{count})" if count > 1 else item for item, count in item_counts.items()])
                message += f"\n{dim_if_goaled(receiver)}**{receiver}** receives: {item_list}"
            message_buffer.append(message)
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
    while True:
        time.sleep(interval)
        send_release_messages() # Send releases first, if any are cued up
        current_lines = fetch_log(url)
        if len(current_lines) > len(previous_lines):
            new_lines = current_lines[len(previous_lines):]
            process_new_log_lines(new_lines)
            if message_buffer:
                send_to_discord('\n'.join(message_buffer))
                logger.info(f"sent {len(message_buffer)} messages to webhook")
                message_buffer.clear()
            previous_lines = current_lines


if __name__ == "__main__":
    logger.info(f"logging messages from AP Room ID {room_id} to webhook {webhook_url}")
    watch_log(log_url, INTERVAL)
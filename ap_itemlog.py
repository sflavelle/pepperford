import datetime
import json
import time
import regex as re
import os
import sys
import ast
import logging
from collections import defaultdict
import requests
import threading
import yaml
from helpers_ap.ap_utils import Game, Item, CollectedItem, Player, PlayerSettings, handle_item_tracking, handle_location_tracking
from word2number import w2n
import paho.mqtt.client as mqtt

# setup logging
logger = logging.getLogger('ap_itemlog')
handler = logging.StreamHandler()
handler.setFormatter(logging.Formatter('[%(name)s %(process)d][%(levelname)s] %(message)s'))
logger.setLevel(logging.INFO)
logger.addHandler(handler)

with open('config.yaml', 'r', encoding='UTF-8') as file:
    cfg = yaml.safe_load(file)

# Disclaimer: Copilot helped me with the initial setup of this file.
# Everything since is my own code. Thank you :-)

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
game = Game()
game.room_id = room_id

# Init MQTT
mqtt = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1, client_id=f"archilog-{room_id}")
mqtt.enable_logger()

mqttparams = (
    cfg['mqtt']['broker'],
    cfg['mqtt']['port'],
    cfg['mqtt']['user'],
    cfg['mqtt']['password']
)
logger.info(f"initialising MQTT with params: {str(mqttparams)}")
mqtt.username_pw_set(mqttparams[2], mqttparams[3])
mqtt.connect(mqttparams[0], mqttparams[1])

def mqtt_send(classtype: Game|Player|PlayerSettings, topic: str, payload, retain: bool = False):
    topicbase = f"archilog/{room_id}"
    topicident = ""

    match type(classtype):
        case "Game":
            topicident = f"game"
        case "Player":
            topicident = f"players/{classtype.name}"
        case _:
            pass

    fulltopic = "/".join([topicbase, topicident, topic])

    logger.debug(f"Sending MQTT payload to topic: {fulltopic}")

    return mqtt.publish(fulltopic, payload, qos=1, retain=retain)



# small functions
goaled = lambda player : game.players[player].is_finished()
dim_if_goaled = lambda p : "-# " if goaled(p) else ""
to_epoch = lambda timestamp : time.mktime(datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f").timetuple())

def process_spoiler_log(seed_url):
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
                if line.startswith("Archipelago"):
                    game.version_generator = line.split(' ')[2]
                    game.seed = parse_to_type(line.split(' ')[-1])
                    logger.info(f"Parsing seed {game.seed}")
                    logger.info(f"Generated on Archipelago version {game.version_generator}")
                else:
                    current_key, value = line.strip().split(':', 1)
                    game.world_settings[current_key.strip()] = parse_to_type(value.lstrip())
                
            case "Players":
                current_key, value = line.strip().split(':', 1)
                if value.lstrip().startswith("[") or value.lstrip().startswith("{"): 
                    try:
                        game.players[working_player].settings[current_key.strip()] = json.loads(value.lstrip())
                    except ValueError:
                        pass
                else:
                    game.players[working_player].settings[current_key.strip()] = parse_to_type(value.lstrip())
            case "Locations":
                if match := regex_patterns['location'].match(line):
                    item_location, sender, item, receiver = match.groups()
                    item_location = item_location.lstrip()
                    if item_location == item and sender == receiver:
                        continue # Most likely an event, can be skipped
                    ItemObject = Item(game.players[sender],game.players[receiver],item,item_location)
                    if sender not in game.spoiler_log: game.spoiler_log.update({sender: {}})
                    game.spoiler_log[sender].update({item_location: ItemObject})
            case "Starting Items":
                if match := regex_patterns['starting_item'].match(line):
                    item, receiver = match.groups()
                    ItemObject = CollectedItem("Archipelago",game.players[receiver],item,"Starting Items")
                    game.players[receiver].items[item] = ItemObject
            case _:
                continue
    logger.info("Done parsing the spoiler log")

def process_new_log_lines(new_lines, skip_msg: bool = False):
    global release_buffer
    global players

    # Regular expressions for different log message types
    regex_patterns = {
        'sent_items': re.compile(r'\[(.*?)]: \(Team #\d\) (\L<players>) sent (.*?(?= to)) to (\L<players>) \((.+)\)$', players=game.players.keys()),
            'item_hints': re.compile(
                r'\[(.*?)]: Notice \(Team #\d\): \[Hint]: (\L<players>)\'s (.*) is at (.*) in (\L<players>)\'s World(?: at (?P<entrance>(.+)))?\. \((?P<hint_status>(.+))\)$', players=game.players.keys()),
        'goals': re.compile(r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has completed their goal\.$'),
        'releases': re.compile(
            r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has released all remaining items from their world\.$'),
        'messages': re.compile(r'\[(.*?)\]: Notice \(all\): (.*?): (.+)$'),
        'joins': re.compile(r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) (?:playing|viewing|tracking) (.+?) has joined. Client\(([0-9\.]+)\), (?P<tags>.+)\.$'),
        'parts': re.compile(r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has left the game\. Client\(([0-9\.]+)\), (?P<tags>.+)\.$'),
    }

    for line in new_lines:
        if match := regex_patterns['sent_items'].match(line):
            timestamp, sender, item, receiver, item_location = match.groups()

            # Mark item as collected 
            try: 
                SentItemObject = Item(game.players[sender],game.players[receiver],item,item_location)
                SentItemObject.found = True
                game.spoiler_log[sender].update({item_location: SentItemObject})
                ReceivedItemObject = CollectedItem(game.players[sender],game.players[receiver],item,item_location)
                if item in game.players[receiver].items: game.players[receiver].items[item].collect(sender, item_location)
                else: game.players[receiver].items[item] = ReceivedItemObject
                # players[sender].send(SentItemObject)
                # game["spoiler"][sender]["locations"][item_location].collect()
            except KeyError as e:
                logger.error(f"""Sent Item Object Creation error. Parsed item name: '{item}', Receiver: '{receiver}', Location: '{item_location}', Error: '{str(e)}'""", e, exc_info=True)
                logger.error(f"Line being parsed: {line}")

            if not skip_msg: logger.info(f"{sender}: {item_location} -> {receiver}'s {item} ({ReceivedItemObject.classification})")

            # By vote of spotzone: if it's filler, don't post it
            if ReceivedItemObject.is_filler() or ReceivedItemObject.is_currency(): continue

            # Update location totals
            game.players[receiver].update_locations(game)
            game.update_locations()

            # If this is part of a release, send it there instead
            if sender in release_buffer and not skip_msg and (to_epoch(timestamp) - release_buffer[sender]['timestamp'] <= 2):
                release_buffer[sender]['items'][receiver].append(ReceivedItemObject)
                logger.debug(f"Adding {item} for {receiver} to release buffer.")
            else:
                # Update item name based on settings for special items
                location = item_location
                if bool(game.players[receiver].settings):
                    try: 
                        item = handle_item_tracking(game, game.players[receiver], item)
                        location = handle_location_tracking(game, game.players[sender], item_location)
                    except KeyError as e:
                        logger.error(f"Couldn't do tracking for item {item} or location {location}:", e, exc_info=True)

                # Update the message appropriately
                if sender == receiver:
                    message = f"**{sender}** found **their own {
                        "hinted " if bool(game.spoiler_log[sender][item_location].hinted) else ""
                        }{item}** ({location})"
                elif bool(game.spoiler_log[sender][item_location].hinted):
                    message = f"{dim_if_goaled(receiver)}{sender} found **{receiver}'s hinted {item}** ({location})"
                else:
                    message = f"{dim_if_goaled(receiver)}{sender} sent **{item}** to **{receiver}** ({location})"
                if not skip_msg: message_buffer.append(message.replace("_",r"\_"))


        elif match := regex_patterns['item_hints'].match(line):
            timestamp = match.groups()[0]
            receiver = match.groups()[1]
            item = match.groups()[2]
            item_location = match.groups()[3]
            sender = match.groups()[4]
            if match.group('entrance'):
                entrance = match.group('entrance')
            else: entrance = None
            if match.group('hint_status'):
                hint_status = match.group('hint_status')

            if hint_status == "found": continue

            SentItemObject = Item(game.players[sender],game.players[receiver],item,item_location,entrance=entrance)
            if item_location not in game.spoiler_log[sender]:
                game.spoiler_log[sender][item_location] = SentItemObject
            else: SentItemObject = game.spoiler_log[sender].get(item_location)

            # Store the hint in the player's hints dictionary
            game.players[sender].add_hint("sending", SentItemObject)
            game.players[receiver].add_hint("receiving", SentItemObject)

            message = f"**[Hint]** **{receiver}'s {item}** is at {item_location} in {sender}'s World{f" (found at {entrance})" if bool(entrance) else ''}."

            match hint_status:
                case "avoid":
                    message += " This item is not useful."
                case "priority":
                    SentItemObject.update_item_classification("progression")
                    message += " **This item will unlock more checks.**"
                case _:
                    pass

            if not skip_msg and game.players[receiver].is_finished() is False and not SentItemObject.found: message_buffer.append(message)
            game.spoiler_log[sender][item_location].hint()


        elif match := regex_patterns['goals'].match(line):
            timestamp, sender = match.groups()
            if sender not in game.players: game.players[sender] = {"goaled": True}
            game.players[sender].goaled = True
            message = f"**{sender} has finished!** That's {len([p for p in game.players.values() if p.is_goaled()])}/{len(game.players)} goaled! ({len([p for p in game.players.values() if p.is_finished()])}/{len(game.players)} including releases)"
            if game.players[sender].collected_locations == game.players[sender].total_locations:
                message += f"\n**Wow!** {sender} 100%ed their game before finishing, too!"
            if not skip_msg: message_buffer.append(message)
        elif match := regex_patterns['releases'].match(line):
            timestamp, sender = match.groups()
            game.players[sender].released = True
            if not skip_msg:
                logging.info("Release detected.")
                release_buffer[sender] = {
                    'timestamp': to_epoch(timestamp),
                    'items': defaultdict(list)
                }
        elif match := regex_patterns['messages'].match(line):
            timestamp, sender, message = match.groups()
            if msg_webhook:
                if message.startswith("!"): continue # don't send commands
                else:
                    if not skip_msg and sender in game.players: 
                        logger.info(f"{sender}: {message}")
                        send_chat(sender, message)

        elif match := regex_patterns['joins'].match(line):
            timestamp, player, playergame, version, tags = match.groups()
            try:
                tags_str = tags
                tags = ast.literal_eval(tags_str)
                game.players[player].tags = tags
            except json.JSONDecodeError:
                logger.error(f"Failed to parse player tags. {player}: {tags_str}")
                tags = tags_str
            if not skip_msg and "TextOnly" not in tags: logger.info(f"{player} ({playergame}) is online.")
            game.players[player].set_online(True, timestamp)
            if "Tracker" in tags:
                if not skip_msg:
                    message = f"{player} is checking what is in logic."
                    message_buffer.append(message)

        elif match := regex_patterns['parts'].match(line):
            timestamp, player, version, tags = match.groups()
            if not skip_msg: logger.info(f"{player} is offline.")
            game.players[player].set_online(False, timestamp)

        else:
            # Unmatched lines
            logger.debug(f"Unparsed line: {line}")

def send_chat(sender, message):
    payload = {
        "username": sender,
        "content": message
    }
    try:
        response = requests.post(msg_webhook, json=payload, timeout=5)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Error sending message to Discord: {e}")


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

        if game.players[receiver].game in currency_matches:
            try:
                for item, count in itemlist.copy().items():
                    if match := currency_matches[game.players[receiver].game][0].match(item):
                        if game.players[receiver].game == "Sonic Adventure 2 Battle":
                            amount = w2n.word_to_num(match.groups()[0]) # why you make me do this
                        else:
                            amount = int(match.groups()[0])
                        currency = currency + (amount * count)
                        del itemlist[item]
                if currency > 0:
                    logger.info(f"Replacing (attempting) currency in {game.players[receiver].game} with '{currency} {currency_matches[game.players[receiver].game][1]}'")
                    itemlist.update({f"{currency} {currency_matches[game.players[receiver].game][1]}": 1})
            except KeyError:
                logger.info(f"No currency handler for {game.players[receiver].game}, but handle_currency matched it anyway somehow!")
                raise

        return itemlist

    for sender, data in release_buffer.copy().items():
        if time.time() - data['timestamp'] > 1:
            message = f"**{sender}** has released their remaining items."
            running_message = message
            for receiver, items in data['items'].items():
                if game.players[receiver].is_finished():
                    continue
                item_counts = defaultdict(int)
                for item in items:
                    if item.is_filler(): continue
                    item_counts[item.name] += 1
                handle_currency(receiver,item_counts)
                item_list = ', '.join(
                    [f"{item} (x{count})" if count > 1 else item for item, count in item_counts.items()])
                running_message += f"\n{dim_if_goaled(receiver)}**{receiver}** receives: {item_list}"
                if len(running_message) > MAX_MSG_LENGTH:
                    send_to_discord(message)
                    message = running_message.replace(message, '')
                    time.sleep(1)
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
        game.players[player[0]] = Player(
            name=player[0],
            game=player[1]
        )
        game.spoiler_log[player[0]] = {}
    del player
    if seed_url:
        logger.info("Processing spoiler log.")
        process_spoiler_log(seed_url)
    previous_lines = fetch_log(url)
    logger.info("Parsing existing log lines before we start watching it...")
    process_new_log_lines(previous_lines, True) # Read for hints etc
    release_buffer = {}
    logger.info(f"Initial log lines: {len(previous_lines)}")
    for p in game.players.values():
        p.update_locations(game)
    game.update_locations()
    logger.info(f"Total Checks: {game.total_locations}")
    logger.info(f"Checks Collected: {game.collected_locations}")
    # classification_thread = threading.Thread(target=save_classifications)
    # classification_thread.start()

    # Send info to MQTT
    game_settings = ["seed", "version_generator", "collected_locations", "total_locations"]
    for key, value in game.items():
        if key in game_settings:
            mqtt_send(game, key, value, True)

    for player in game.players.values():
        for key, value in player.__dict__.items():
            if key in ["items", "locations", "hints"]:
                mqtt_send(player, key, json.dumps(value))
            else:
                mqtt_send(player, key, str(value), True)
    del game_settings

    logger.info("Ready!")
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
    # mqtt.loop_start()
    watch_log(log_url, INTERVAL)
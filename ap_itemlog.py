import requests
import datetime
import time
import re
import os
import logging
from collections import defaultdict

# setup logging
logger = logging.getLogger('ap_itemlog')
handler = logging.StreamHandler()
logger.setLevel(logging.INFO)
logger.addHandler(handler)

# I got Copilot to help me write most of this file. I'm so sorry

# URL of the log file and Discord webhook URL from environment variables
log_url = os.getenv('LOG_URL')
webhook_url = os.getenv('WEBHOOK_URL')
session_cookie = os.getenv('SESSION_COOKIE')

if not (bool(log_url) or bool(webhook_url) or bool(session_cookie)):
    logger.error("Something required isn't configured properly!")
    exit(1)

room_id = log_url.split('/')[-1]
hostname = log_url.split('/')[2]

api_url = f"https://{hostname}/api/room_status/{room_id}"

# Time interval between checks (in seconds)
interval = 20

# Regular expressions for different log message types
regex_patterns = {
    'sent_items': re.compile(r'\[(.*?)\]: \(Team #\d\) (.*?) sent (.*) to (.*?) \((.*?)\)$'),
    'item_hints': re.compile(
        r'\[(.*?)\]: Notice \(Team #\d\): \[Hint\]: (.*?)\'s (.*) is at (.*?) in (.*?)\'s World\.(?<! \(found\))$'),
    'goals': re.compile(r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has completed their goal\.$'),
    'releases': re.compile(
        r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has released all remaining items from their world\.$')
}

# Buffer to store release and related sent item messages
release_buffer = {}
message_buffer = []

# Store for item_hints
item_hints_store = {}
players = {}

# small functions
goaled = lambda player : "goaled" in players[player] and players[player]["goaled"] == True
dim_if_goaled = lambda p : "-# " if goaled(p) else ""
to_epoch = lambda timestamp : time.mktime(datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f").timetuple())

def process_new_log_lines(new_lines, skip_msg: bool = False):
    global release_buffer
    global players
    for line in new_lines:
        if match := regex_patterns['sent_items'].match(line):
            timestamp, sender, item, receiver, item_location = match.groups()
            if sender not in players: players[sender] = {}
            if receiver not in players: players[receiver] = {}
            if sender in release_buffer and (to_epoch(timestamp) - release_buffer[sender]['timestamp'] <= 2):
                    release_buffer[sender]['items'][receiver].append(item)
                    if not skip_msg: logger.info(f"Adding {item} for {receiver} to release buffer.")
            else:
                if sender == receiver:
                    message = f"**{sender}** found their own {"hinted " if (f"{sender} - {item_location}" in item_hints_store and item in item_hints_store[f"{sender} - {item_location}"]) else ""}**{item}** ({item_location})"
                    if f"{sender} - {item_location}" in item_hints_store and item in item_hints_store[f"{sender} - {item_location}"]:
                        del item_hints_store[f"{sender} - {item_location}"]
                elif f"{sender} - {item_location}" in item_hints_store and item in item_hints_store[f"{sender} - {item_location}"]:
                    message = f"{dim_if_goaled(receiver)}{sender} found **{receiver}'s hinted {item}** ({item_location})"
                    del item_hints_store[f"{sender} - {item_location}"]
                else:
                    message = f"{dim_if_goaled(receiver)}{sender} sent **{item}** to **{receiver}** ({item_location})"
                if not skip_msg: message_buffer.append(message)
        elif match := regex_patterns['item_hints'].match(line):
            timestamp, receiver, item, item_location, sender = match.groups()
            if sender not in players: players[sender] = {"goaled": False}
            if receiver not in players: players[receiver] = {"goaled": False}
            if receiver not in item_hints_store:
                item_hints_store[f"{sender} - {item_location}"] = set()
            item_hints_store[f"{sender} - {item_location}"].add(item)
            message = f"**[Hint]** **{receiver}'s {item}** is at {item_location} in {sender}'s World."
            if not skip_msg: message_buffer.append(message)
        elif match := regex_patterns['goals'].match(line):
            timestamp, sender = match.groups()
            if sender not in players: players[sender] = {"goaled": True}
            message = f"**{sender} has finished!**"
            players[sender]["goaled"] = True
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
        response = requests.post(webhook_url, json=payload)
        response.raise_for_status()
    except requests.RequestException as e:
        logging.error(f"Error sending message to Discord: {e}")


def send_release_messages():
    global release_buffer


    for sender, data in release_buffer.items():
        if time.time() - data['timestamp'] > interval:
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
        response = requests.get(url, cookies=cookies)
        response.raise_for_status()
        return response.text.splitlines()
    except requests.RequestException as e:
        logger.error(f"Error fetching log file: {e}")
        return []


def watch_log(url, interval):
    global release_buffer
    global players
    logger.info("Fetching room info.")
    for player in requests.get(api_url).json()["players"]:
        players[player[0]] = {}
        players[player[0]]["game"] = player[1]
    previous_lines = fetch_log(url)
    process_new_log_lines(previous_lines, True) # Read for hints etc
    release_buffer = {}
    logger.info(f"Initial log lines: {len(previous_lines)}")
    logger.info(f"Currently active hints: {len(item_hints_store)}")

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
    logger.info(f"logging messages from {log_url} to webhook {webhook_url}")
    watch_log(log_url, interval)
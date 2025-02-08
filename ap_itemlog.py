import requests
import time
import re
import os
from collections import defaultdict

# I got Copilot to help me write this file. I'm so sorry

# URL of the log file and Discord webhook URL from environment variables
log_url = os.getenv('LOG_URL')
webhook_url = os.getenv('WEBHOOK_URL')
session_cookie = os.getenv('SESSION_COOKIE')

# Time interval between checks (in seconds)
interval = 20

# Regular expressions for different log message types
regex_patterns = {
    'sent_items': re.compile(r'\[(.*?)\]: \(Team #\d\) (.*?) sent (.*?) to (.*?) \((.*?)\)'),
    'item_hints': re.compile(
        r'\[(.*?)\]: Notice \(Team #\d\): \[Hint\]: (.*?)\'s (.*?) is at (.*?) in (.*?)\'s World\.'),
    'goals': re.compile(r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has completed their goal\.'),
    'releases': re.compile(
        r'\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has released all remaining items from their world\.')
}

# Buffer to store release and related sent item messages
release_buffer = {}
message_buffer = []


def process_new_log_lines(new_lines):
    global release_buffer
    for line in new_lines:
        if match := regex_patterns['sent_items'].match(line):
            timestamp, sender, item, receiver, item_location = match.groups()
            message = f"{sender} sent {item} to {receiver} ({item_location})"
            if sender in release_buffer and (time.time() - release_buffer[sender]['timestamp'] <= 2):
                release_buffer[sender]['items'][receiver].append(item)
            else:
                message_buffer.append(message)
        elif match := regex_patterns['item_hints'].match(line):
            timestamp, receiver, item, item_location, sender = match.groups()
            message = f"**[Hint]** {receiver}'s {item} is at {item_location} in {sender}'s World."
            message_buffer.append(message)
        elif match := regex_patterns['goals'].match(line):
            timestamp, sender = match.groups()
            message = f"**{sender} has finished!**"
            message_buffer.append(message)
        elif match := regex_patterns['releases'].match(line):
            timestamp, sender = match.groups()
            release_buffer[sender] = {
                'timestamp': time.time(),
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
        print(f"Error sending message to Discord: {e}")


def send_release_messages():
    global release_buffer
    for sender, data in release_buffer.items():
        if time.time() - data['timestamp'] > 2:
            message = f"**{sender}** has released their remaining items."
            for receiver, items in data['items'].items():
                item_counts = defaultdict(int)
                for item in items:
                    item_counts[item] += 1
                item_list = ', '.join(
                    [f"{item} (x{count})" if count > 1 else item for item, count in item_counts.items()])
                message += f"\n**{receiver}** receives: {item_list}"
            message_buffer.append(message)
    release_buffer = {}


def fetch_log(url):
    try:
        cookies = {'session': session_cookie}
        response = requests.get(url, cookies=cookies)
        response.raise_for_status()
        return response.text.splitlines()
    except requests.RequestException as e:
        print(f"Error fetching log file: {e}")
        return []


def watch_log(url, interval):
    previous_lines = fetch_log(url)
    print(f"Initial log lines: {len(previous_lines)}")

    while True:
        time.sleep(interval)
        current_lines = fetch_log(url)
        if len(current_lines) > len(previous_lines):
            new_lines = current_lines[len(previous_lines):]
            process_new_log_lines(new_lines)
            send_release_messages()
            if message_buffer:
                send_to_discord('\n'.join(message_buffer))
                message_buffer.clear()
            previous_lines = current_lines


if __name__ == "__main__":
    watch_log(log_url, interval)
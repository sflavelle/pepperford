from ast import List
import ast
import fnmatch
import json
import logging
import os
import pathlib
import random
import socket
import sys
import threading
import time
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Any, Optional

import dateparser
import graphviz
import psycopg2 as psql
import regex as re
import requests
import yaml
from flask import Flask, Response, jsonify, request
from flask.logging import default_handler
from flask_cors import CORS
from word2number import w2n

from cmds.ap_scripts.emitter import event_emitter
from cmds.ap_scripts.game_handlers import (
    HandlerContext,
    SendEvent,
    dispatch,
    dispatch_announcements,
    get_handler_fuzzy,
)
from cmds.ap_scripts.requirements import format_requirements
from cmds.ap_scripts.utils import (
    Game,
    Item,
    Location,
    Player,
    PlayerSettings,
    handle_item_tracking,
    handle_location_hinting,
    handle_location_tracking,
)

DEBUG = (
    os.getenv("DEBUG_MODE", "").lower() if os.getenv("DEBUG_MODE", "") != "" else False
)
if DEBUG in ["1", "true", "yes", "on"]:
    DEBUG = True
else:
    DEBUG = False

# setup logging
logger = logging.getLogger("ap_itemlog")
handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter("[%(name)s %(process)d][%(levelname)s] %(message)s")
)
if DEBUG:
    logger.setLevel(logging.DEBUG)
else:
    logger.setLevel(logging.INFO)
logger.addHandler(handler)

# Silksong's conditional-progression tables now live with its handler:
# cmds/ap_scripts/game_handlers/silksong.py


with open("config.yaml", "r", encoding="UTF-8") as file:
    cfg = yaml.safe_load(file)

sqlcfg = cfg["bot"]["psql"]
try:
    sqlcon = psql.connect(
        dbname=sqlcfg["database"],
        user=sqlcfg["user"],
        password=sqlcfg["password"] if "password" in sqlcfg else None,
        host=sqlcfg["host"],
        port=sqlcfg["port"],
    )
except psql.OperationalError:
    logger.warning(
        "Could not connect to the database, some features will be unavailable. Check your configuration and database status."
    )
    sqlcon = False

# Disclaimer: Copilot helped me with the initial setup of this file.
# Everything since is my own code. Thank you :-)

# URL of the log file and Discord webhook URL from environment variables
log_url = os.getenv("LOG_URL")
webhook_urls = [os.getenv("WEBHOOK_URL")]
session_cookie = os.getenv("SESSION_COOKIE")

# Extra info for additional features
seed_url = os.getenv("SPOILER_URL")
msg_webhooks = [os.getenv("MSGHOOK_URL")]
meta_webhook = [os.getenv("METAHOOK_URL")]

# Pull extra configuration if this itemlog is stored in config.yaml, by checking the log_url
if (
    cfg is not None
    and "bot" in cfg
    and "archipelago" in cfg["bot"]
    and "itemlogs" in cfg["bot"]["archipelago"]
):
    for log in cfg["bot"]["archipelago"]["itemlogs"]:
        if log["log_url"] == log_url:
            if "webhooks" in log and len(log["webhooks"]) > 1:
                webhook_urls.extend(log["webhooks"][1:])
            if "msghooks" in log and len(log["msghooks"]) > 1:
                msg_webhooks.extend(log["msghooks"][1:])
            break

if not (bool(log_url) or bool(webhook_urls) or bool(session_cookie)):
    logger.error("Something required isn't configured properly!")
    for var in [
        ("LOG_URL", log_url),
        ("WEBHOOK_URL", webhook_urls),
        ("SESSION_COOKIE", session_cookie),
    ]:
        logger.error(f"{var[0]}: {var[1]}")
    sys.exit(1)

room_id = log_url.split("/")[-1]
hostname = log_url.split("/")[2]
seed_id = None

log_url = f"https://{hostname}/log/{room_id}"
api_url = f"https://{hostname}/api/room_status/{room_id}"
spoiler_url = None

if bool(seed_url):
    seed_id = seed_url.split("/")[-1]
    spoiler_url = f"https://{hostname}/dl_spoiler/{seed_id}"

seed_address = None

# Add file logger now that room_id is set
if not os.path.exists("logs"):
    os.makedirs("logs")
logfile = logging.FileHandler(f"logs/room_{room_id}.log", encoding="UTF-8")
logfile.setFormatter(logging.Formatter("[%(asctime)s][%(levelname)s] %(message)s"))
logger.addHandler(logfile)

# Time interval between checks (in seconds)
INTERVAL = 60
# Maximum Discord message length in characters
MAX_MSG_LENGTH = 2000
# How long to wait for release items
RELEASE_DELTA = timedelta(seconds=2)

# Timezones for timestamp parsing
timezones = {
    "archipelago.gg": "Etc/UTC",
    "rando.thegeneral.chat": "America/Chicago",
    "neurario.com": "Australia/Melbourne",
}

# Get the timezone from the machine this is running on
local_timezone = time.tzname[time.daylight]

# Buffer to store release and related sent item messages
release_buffer = {}
collect_buffer = {}
message_buffer = []

# Store for players, items, settings
game = Game()
game.hostname = hostname
game.room_id = room_id
game.seed_id = seed_id
start_time = None

# small functions
goaled = lambda player: game.players[player].is_finished()
dim_if_goaled = lambda p: "-# " if goaled(p) else ""


def parse_to_datetime(timestamp_str: str) -> datetime:
    return dateparser.parse(
        timestamp_str[:-3],  # strip milliseconds
        settings={
            "TIMEZONE": timezones.get(hostname, "Etc/UTC"),
            "TO_TIMEZONE": local_timezone,
            "RETURN_AS_TIMEZONE_AWARE": True,
        },
    )


def join_words(words: list):
    if len(words) is 0: return words
    if type(words) != list:
        logger.error(f"join_words: Data is not List type: '{words}' (is {type(words)})")
        return words
    if len(words) > 2:
        return "%s, and %s" % (", ".join(words[:-1]), words[-1])
    elif len(words) == 2:
        return " and ".join(words)
    else:
        return str(words)


def is_int(s) -> bool:
    try:
        int(s)
        return True
    except ValueError:
        return False


# Spoiler Log Processing


def process_spoiler_log(seed_url):
    global game
    global start_time

    spoiler_url = f"https://{hostname}/dl_spoiler/{seed_id}"

    spoiler_log = requests.get(spoiler_url, timeout=10)
    spoiler_text = spoiler_log.text.split("\n")

    parse_mode = "Seed Info"
    working_player = None
    current_sphere: int = None

    regex_patterns = {
        "location": re.compile(r"^\s*(.+) \((.+?)\): (.+) \((.+?)\)$"),
        "starting_item": re.compile(r"^\s*(.+) \((.+?)\)$"),
        "pokemon_locations": re.compile(r"^Wild Pokemon \((.+?)\):$"),
    }

    def parse_to_type(s):
        # Try int, float, bool, list, dict, or fallback to str
        try:
            return ast.literal_eval(s)
        except Exception:
            return s

    def parse_line(line):
        current_key, value = line.strip().split(":", 1)
        value_str = value.lstrip()
        key = current_key.strip().replace("_", " ")

        return key, value_str

    def smart_split(s):
        # Split on commas, ignoring those inside [], (), or {}
        parts = []
        bracket_level = 0
        curr = []
        for char in s:
            if char in "[({":
                bracket_level += 1
            elif char in "])}":
                bracket_level -= 1
            if char == "," and bracket_level == 0:
                parts.append("".join(curr).strip())
                curr = []
            else:
                curr.append(char)
        if curr:
            parts.append("".join(curr).strip())
        return parts

    def parse_value(value_str):
        # Dict-like pattern: key: value, key: value, ...
        if (
            "," in value_str
            and ":" in value_str
            and not value_str.startswith("[")
            and not value_str.startswith("{")
        ):
            items = smart_split(value_str)
            result = {}
            for item in items:
                if ":" in item:
                    k, v = item.split(":", 1)
                    v_parsed = parse_to_type(v.strip())
                    result[k.strip()] = v_parsed
                else:
                    result[item] = None
            return result
        # Otherwise, try to parse as list/dict/etc.
        return parse_to_type(value_str)

    for line in spoiler_text:
        line = str(line)
        if len(line) == 0:
            continue

        if line.startswith("Archipelago Version"):
            parse_mode = "Seed Info"
        if (
            line.startswith("Player ")
            and working_player != line.strip().split(":", 1)[1].strip()
        ):
            if any(
                line.startswith(broken)
                for broken in ["Player Sacrifice Limit", "Player Summon Limit"]
            ):
                continue  # for now, TODO handle game options starting with "Player"
            parse_mode = "Players"

            working_player = line.strip().split(":", 1)[1].strip()
            logger.info(f"Parsing settings for player {working_player}")
        if line == "Locations:":
            parse_mode = "Locations"
            logger.info("Parsing multiworld locations")
            continue
        if line == "Starting Items:":
            parse_mode = "Starting Items"
            logger.info("Parsing starting items")
        if line in [
            "Entrances:",
            "Medallions:",
            "Fairy Fountain Bottle Fill:",
            "Shops:",
            "Level Layout",
            "Animal Friends",
            "MIAB Locations:",
            "Charm Notches:",
        ]:
            parse_mode = None
        if any(
            line.startswith(category)
            for category in [
                "Dungeon Entrances",
                "Spicy Mycena Waffles map",
                "Pokémon locations",
            ]
        ):
            parse_mode = None
            continue
        if line.startswith("Spoiler and info for [Jigsaw]"):
            parse_mode = "Jigsaw Info"
            working_player_num = 0
            try:
                working_player_num = int(line.rsplit(" ", 1)[-1].strip()) - 1
                working_player = game.players[
                    list(game.players.keys())[working_player_num]
                ].name
                logger.info(f"Parsing Jigsaw settings for player {working_player}")
            except (ValueError, IndexError):
                logger.error(f"Error parsing Jigsaw player number from line: {line}")
                working_player = None
            continue
        if match := regex_patterns["pokemon_locations"].match(line):
            parse_mode = "Pokemon Locations"
            working_player = match.group(1)
            game.players[working_player].settings["Wild Pokemon Locations"] = {}
            logger.info(f"Parsing Pokemon locations for player {working_player}")
        if line.strip() == "Playthrough:":
            parse_mode = "Spheres"
            game.spheres = {}
        if line.strip() == "Unreachable Progression Items:":
            parse_mode = None  # we don't need to parse this information for item tracking, and it can be complex to do so accurately
            continue

        match parse_mode:
            case "Seed Info":
                if line.startswith("Celeste (Open World) APWorld"):
                    continue  # don't need to record apworld version information
                if line.startswith("Archipelago"):
                    game.version_generator = line.split(" ")[2]
                    game.seed = parse_to_type(line.split(" ")[-1])
                    logger.info(f"Parsing seed {game.seed}")
                    logger.info(
                        f"Generated on Archipelago version {game.version_generator}"
                    )
                    if sqlcon:
                        with sqlcon.cursor() as cursor:
                            game.pushdb(cursor, "archipelago.ap_all_rooms", "seed", game.seed)
                            game.pushdb(
                                cursor,
                                "archipelago.ap_all_rooms",
                                "version",
                                game.version_generator,
                            )
                            sqlcon.commit()
                else:
                    try:
                        current_key, value = line.strip().split(":", 1)
                        if "," in value.lstrip():
                            # Parse as a list
                            game.world_settings[current_key.strip()] = [
                                parse_to_type(v.strip())
                                for v in value.lstrip().split(",")
                            ]
                        else:
                            game.world_settings[current_key.strip()] = parse_to_type(
                                value.lstrip()
                            )
                    except ValueError as e:
                        logger.error("Error parsing line:")
                        logger.error(line)
                        logger.error(f"Error: {e}")
                        continue

            case "Players":
                try:
                    key, value_str = parse_line(line)
                    if key.startswith("Player "):
                        # Extract the player ID from Player header
                        if is_int(key.split(" ", 1)[1]):
                            game.players[working_player].id = int(key.split(" ", 1)[1])
                            continue
                    if (
                        key
                        in [
                            "Excluded Locations",
                            "Local Items",
                            "Non-Local Items",
                            "Priority Locations",
                            "Start Hints",
                            "Start Location Hints",
                        ]
                        and len(value_str) == 0
                    ):
                        game.players[working_player].settings[key] = list()
                        continue
                    if (
                        key
                        in [
                            "Item Links",
                            "Plando Items",
                            "Start Inventory",
                            "Start Inventory from Pool",
                        ]
                        and len(value_str) == 0
                    ):
                        game.players[working_player].settings[key] = dict()
                        continue
                    game.players[working_player].settings[key] = parse_value(value_str)
                    if (
                        type(game.players[working_player].settings[key]) == str
                        and "," in game.players[working_player].settings[key]
                    ):
                        # Comma-separated string (no brackets), parse as list
                        game.players[working_player].settings[key] = smart_split(
                            game.players[working_player].settings[key]
                        )

                    # Mycena Settings
                    if key == "Required eggs" and game.players[working_player].game == "SMW: Spicy Mycena World":
                        value = value.split(" ")
                        game.players[working_player].settings["Yoshi Egg Count"] = value[-1]

                except ValueError as e:
                    logger.error(f"Error parsing line:")
                    logger.error(line)
                    logger.error(f"Error: {e}")
                    continue
            case "Locations":
                if match := regex_patterns["location"].match(line):
                    item_location, sender, item, receiver = match.groups()
                    item_location = item_location.lstrip()
                    ItemObject = game.get_or_create_item(
                        game.players[sender],
                        game.players[receiver],
                        item,
                        item_location,
                        received_timestamp=start_time,
                    )

                    if item_location == item and sender == receiver:
                        continue  # Most likely an event, can be skipped
                    if ItemObject.location.is_checkable is False:
                        # If the item is not checkable, we don't need to store it
                        # But we can't delete it just yet until the checkable database is more complete
                        # TODO uncomment this when this is safer to do
                        # del ItemObject
                        # continue
                        pass
                    else:
                        if game.players[sender].name == sender:
                            game.players[sender].add_spoiler(ItemObject)
                        if game.players[receiver].name == receiver:
                            game.players[receiver].add_spoiler(ItemObject)

                    ItemObject.location.db_add_location()

                    if sender not in game.spoiler_log:
                        game.spoiler_log.update({sender: {}})
                    game.spoiler_log[sender].update({item_location: ItemObject})
            case "Starting Items":
                if match := regex_patterns["starting_item"].match(line):
                    item, receiver = match.groups()
                    ItemObj = game.get_or_create_item(
                            "Archipelago",
                            game.players[receiver],
                            item,
                            "Starting Items",
                            received_timestamp=start_time,
                    )
                    game.players[receiver].inventory.append(ItemObj)
                    game.add_to_sphere(ItemObj, 0, game.players[receiver])
            case "Jigsaw Info":
                try:
                    key, value_str = parse_line(line)
                    game.players[working_player].settings[key] = parse_to_type(
                        value_str
                    )
                except ValueError as e:
                    if line.startswith("Spoiler and info for [Jigsaw]"):
                        # Not a problem, just the header
                        continue
                    else:
                        logger.error(f"Error parsing Jigsaw line: {line}")
                        raise e
            case "Pokemon Locations":
                # Some Pkmn games list the locations of wild Pokemon in the spoiler log
                if match := regex_patterns["location"].match(line):
                    try:
                        key, value_str = parse_line(line)
                        game.players[working_player].settings["Wild Pokemon Locations"][
                            key
                        ] = parse_to_type(value_str)
                    except ValueError as e:
                        logger.error(f"Error parsing Pokemon location line: {line}")
                        logger.error(f"Error: {e}")
                        continue
            case "Spheres":
                # Sphere indicator line: `0: {`
                if match := re.match(r"^(\d+): \{$", line):
                    previous_sphere = current_sphere
                    if previous_sphere is not None:
                        logger.info(f"Parsed sphere {previous_sphere} with {sphere_item_count} items")

                    sphere_item_count: int = 0
                    current_sphere = int(match.group(1))
                    logger.info(f"Parsing sphere {current_sphere}")
                    game.spheres[current_sphere] = []
                    continue
                elif line == "}" or line == "Playthrough:": 
                    continue # end of sphere/junk messages
                # Sphere 0 is the starting inventory
                elif match := regex_patterns["location"].match(line):
                    item_location, sender, item, receiver = match.groups()
                    item_location = item_location.lstrip()
                    s_player = game.get_player(sender)
                    r_player = game.get_player(receiver)
                    item = game.get_or_create_item(s_player,r_player,item,item_location,get_only=True)
                    if item is not None:
                        sphere_item_count += 1
                        game.add_to_sphere(item, current_sphere, s_player)
                    else:
                        logger.warning(f"Failed to find item for sphere {current_sphere}: {item} from {sender} to {receiver} at {item_location}")
                elif match := regex_patterns["starting_item"].match(line):
                    item, receiver = match.groups()
                    r_player = game.get_player(receiver)
                    item = game.get_or_create_item("Archipelago",r_player,item,"Starting Items",get_only=True)
                    if item is not None:
                        sphere_item_count += 1
                        game.add_to_sphere(item, current_sphere, r_player)
                    else:
                        logger.warning(f"Failed to find item for sphere {current_sphere}: {item} from {sender} to {receiver} at {item_location}")
                else:
                    logger.warning(f"Unrecognized line in sphere {current_sphere}: {line}")
                    continue

            case _:
                continue

    # Handle odd settings (cast to bool, etc)
    for player in game.players.values():
        for setting, value in player.settings.items():
            if type(value) != str:
                continue
            try:
                if value.lower() in ["yes", "on"]:
                    player.settings[setting] = True
                elif value.lower() in ["no", "off"]:
                    player.settings[setting] = False
                else:
                    pass
            except ValueError:
                pass

    # Some game-specific handling
    for player in game.players.values():
        if player.game == "gzDoom":
            try:
                # Fix Win Conditions dict breaking
                original = player.settings["Win conditions"]
                winsettings = {"nrof-maps": None, "specific-maps": None}
                logger.info(f"Attempting to parse GZDoom win conditions: {original}")
                player.settings["Win conditions"] = {}

                # match something like 'nrof-maps: 175'
                option_regex = re.compile(r"^ ?(.+?): (.+)$")

                for option in original.split(","):
                    logger.info(f"Option: '{option}'")

                    if match := option_regex.match(option):
                        key, val = match.groups()
                        player.settings["Win conditions"][key] = val

                player.settings["Win conditions"] = winsettings
            except ValueError as err:
                pass
            try:
                # Determine the real Included Levels list by Level Access items
                levelaccess_mapname_match = re.compile(r"Level Access \((.+?)\)")
                # if player.settings['Win conditions'].get('specific-maps', False):
                #     goal_patterns = list(player.settings['Win conditions']['specific-maps'])
                #
                #     included_working_list = []
                #     goal_working_list = []
                #
                #     for location in player.locations.keys():
                #         map_name = None
                #         if not location.startswith("Level Access ("): continue
                #
                #         if match := levelaccess_mapname_match.match(str(location)):
                #             map_name = match.group(1)
                #             included_working_list.add(map_name)
                #
                #             # If the map matches any of the goal patterns, add it to the goal list
                #             for pattern in goal_patterns:
                #                 if fnmatch.fnmatch(map_name, pattern):
                #                     goal_working_list.add(map_name)
                #
                #     # Remove duplicates and update Included Levels
                #     complete_level_list = included_working_list
                #     logger.info(f"Expanded gzDoom Included Levels for {player.name}: {complete_level_list}")
                #     logger.info(f"Expanded gzDoom Goal Levels for {player.name}: {goal_working_list}")
                #     player.settings["Included levels"] = complete_level_list
                #     player.stats.set_stat("all_levels", complete_level_list)
                #     player.stats.set_stat("goal_levels", goal_working_list)
                #     player.settings['Win conditions']['specific-maps'] = goal_working_list
            except KeyError as err:
                logger.error(err, exc_info=True)
        if player.game == "SMW: Spicy Mycena Waffles" and not len(player.slot_data):
            try:
                total_egg_requirement = int(player.settings["Yoshi Egg Count"] + len([l for l in player.spoilers["locations"] if l.name.endswith("(Hidden Egg)")]))
                logger.info(f"Player {player.name}'s Golden Egg Requirement calculated as {total_egg_requirement}")
            except Exception as err:
                logger.error(f"Failed to calculate true Golden Egg Requirement for {player.name}: {str(err)}", err)

    # One call per slot now that its spoiler and settings are loaded - the home for per-seed setup
    # that is not a per-send decision (target values from slot_data, requirements derived from
    # spoiler locations, name tables, layout priming). Handlers keep results in ctx.state().
    for player in game.players.values():
        if not isinstance(player, Player) or not player.settings:
            continue
        if get_handler_fuzzy(player.game) is None:
            continue
        dispatch(
            player.game,
            "on_slot_loaded",
            _handler_context_for(None, player=player),
            default=None,
        )

    logger.info("Done parsing the spoiler log")


def _handler_context_for(
    item: Any, timestamp: Any = None, item_index: Any = None, player: Any = None
) -> HandlerContext:
    """Build the HandlerContext for one event (protocol v2 game handlers).

    `player` selects whose settings the context carries: the location's owner on the send path (the
    slot that owns the check, e.g. where "Completion Goal" lives for a Niko friend check), or
    `item.receiver` for classification, which is keyed on the item's own game and the receiver's
    options - matching what live_classification() has always read.

    `item_index` is the line's absolute position in the room's log buffer (the counter
    `archipelago.ap_all_rooms.last_line` stores), passed down from process_new_log_lines so a handler's
    dedupe_key survives a replayed prefix.
    """
    location = getattr(item, "location", None)
    owner = player if player is not None else getattr(location, "player", None)
    to_epoch = getattr(timestamp, "timestamp", None)
    if callable(to_epoch):
        ts: Optional[float] = float(to_epoch())
    elif isinstance(timestamp, (int, float)):
        ts = float(timestamp)
    else:
        ts = None
    return HandlerContext(
        game=getattr(owner, "game", None)
        or getattr(location, "game", None)
        or getattr(item, "game", None)
        or "",
        player=owner,
        itemlog=globals().get("game"),
        slot=getattr(owner, "id", None),
        player_name=getattr(owner, "name", None),
        seed_name=globals().get("seed_address"),
        timestamp=ts,
        item_index=item_index,
        settings=getattr(owner, "settings", None) or {},
    )


def process_new_log_lines(new_lines, skip_msg: bool = False, line_offset: int = 0):
    """Process a slice of the room's log buffer.

    `line_offset` is the position of `new_lines[0]` in the full buffer, so a handler can key on the
    absolute line number (the same counter `archipelago.ap_all_rooms.last_line` stores).
    """
    global release_buffer
    global players
    global seed_address
    global start_time

    # Regular expressions for different log message types
    regex_patterns = {
        "sent_items": re.compile(
            r"\[(.*?)]: \(Team #\d\) (\L<players>) sent (.*?(?= to)) to (\L<players>) \((.+)\)$",
            players=game.players.keys(),
        ),
        "item_hints": re.compile(
            r"\[(.*?)]: Notice \(Team #\d\): \[Hint]: (\L<players>)\'s (.*) is at (.*) in (\L<players>)\'s World(?: at (?P<entrance>(.+)))?\. \((?P<hint_status>(.+))\)$",
            players=game.players.keys(),
        ),
        "goals": re.compile(
            r"\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has completed their goal\.$"
        ),
        "releases": re.compile(
            r"\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has released all remaining items from their world\.$"
        ),
        "collects": re.compile(
            r"\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has collected their items from other worlds\.$"
        ),
        "messages": re.compile(r"\[(.*?)\]: Notice \(all\): (.*?): (.+)$"),
        "room_shutdown": re.compile(r"\[(.*?)\]: Shutting down due to inactivity.$"),
        "room_spinup": re.compile(r"\[(.*?)\]: Hosting game at (.+?)$"),
        "joins": re.compile(
            r"\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) (playing|viewing|tracking) (.+?) has joined. Client\(([0-9\.]+)\), (?P<tags>.+)\.$"
        ),
        "parts": re.compile(
            r"\[(.*?)\]: Notice \(all\): (.*?) \(Team #\d\) has left the game\. Client\(([0-9\.]+)\), (?P<tags>.+)\.$"
        ),
    }

    def live_classification(item, item_index=None, timestamp=None):
        response = item.classification
        setting = item.receiver.settings

        # A game handler may resolve any class the database cannot answer: rows stored as NULL and
        # rows stored as `conditional progression` (progression only under some options). The inline
        # rules below remain the fallback for games that have not migrated yet.
        if response is None or response == "conditional progression":
            ctx = None
            proposal = None
            if get_handler_fuzzy(item.game) is not None:
                ctx = _handler_context_for(item, timestamp, item_index, player=item.receiver)
                proposal = dispatch(
                    item.game, "resolve_classification", ctx, item, response, default=None
                )
                if proposal is None and response == "conditional progression":
                    # Deprecated pre-v2.1 hook name, still honoured.
                    proposal = dispatch(
                        item.game,
                        "resolve_conditional_progression",
                        ctx,
                        item,
                        default=None,
                    )
            if proposal is not None:
                response = proposal.classification

        if response == "conditional progression":
            # Progression in certain settings, otherwise useful/filler
            # if item.game == "gzDoom":
            #     # Weapons : extra copies can be filler
            #     if isinstance(item, Item) and player.get_item_count(item.name) > 1:
            #         response = "filler"
            # Silksong's conditional items are resolved by its own handler now: the tables and the
            # world's conditions live in cmds/ap_scripts/game_handlers/silksong.py.
            # After checking everything, if not re-classified, it's probably progression
            if response == "conditional progression":
                response = "progression"

        # Persist the outcome for every path, not just the conditional one: a handler that resolved a
        # NULL or conditional row replaced `response`, and the write used to sit inside the guard above.
        if response is not None:
            item.classification = response
        return item

    for line_no, line in enumerate(new_lines, start=line_offset):
        line_start_time = time.time_ns()  # for performance logging
        if match := regex_patterns["sent_items"].match(line):
            timestamp, sender, item, receiver, item_location = match.groups()

            timestamp = parse_to_datetime(timestamp)

            # Mark item as collected
            try:
                Item = game.get_or_create_item(
                    game.players[sender],
                    game.players[receiver],
                    item,
                    item_location,
                    received_timestamp=timestamp,
                )
                if Item.found:
                    continue  # log attempted to reprocess an item already found
                game.players[sender].collect_item(Item)
                game.spoiler_log[sender].update({item_location: Item})

                # If it was hinted, update the player's hint table
                for hintitem in game.players[receiver].hints["receiving"]:
                    if item_location == hintitem.location:
                        del hintitem
                        Item.hinted = True
                        break
                for hintitem in game.players[sender].hints["sending"]:
                    if item_location == hintitem.location:
                        del hintitem
                        Item.hinted = True
                        break

            except KeyError as e:
                logger.error(
                    f"""Sent Item Object Creation error. Parsed item name: '{item}', Receiver: '{receiver}', Location: '{item_location}', Error: '{str(e)}'""",
                    e,
                    exc_info=True,
                )
                logger.error(f"Line being parsed: {line}")

            # Update location totals
            Item.location.db_add_location(True)
            game.players[sender].update_locations(game)
            game.update_locations()

            # Live-Classify if the item is Conditional Progression
            Item = live_classification(Item, line_no, timestamp)

            icon: str = None
            item_with_icon = lambda item, icon: f"{icon} {item}" if bool(icon) else item

            match Item.classification:
                case "progression":
                    icon = "<:progression:1424290927735869461>"
                case "trap":
                    icon = "<:trapitem:1450760161286295734>"
                case "filler":
                    icon = "<:filler:1461179676365164729>"
                case "currency":
                    icon = "<:coin:1469865094665207879>"
                case None:
                    icon = "<:unclassified:1450498207032283357>"
                case _:
                    pass

            if not skip_msg:
                logger.info(
                    f"{sender}: ({str(game.players[sender].collected_locations)}/{str(game.players[sender].total_locations)}/{str(round(game.players[sender].collection_percentage, 2))}%) {item_location} -> {receiver}'s {item} ({Item.classification})"
                )

            # By vote of spotzone: if it's filler, don't post it.
            # 2026-01-15 amendment: specific locations might be important (goal-bearing). Game
            # handlers decide that now - Spyro 3 Skill Points (spyro3.py), Simon Tatham's
            # (simon_tathams.py) and Here Comes Niko! friends (here_comes_niko.py) are the first
            # migrations. One context is built for the whole send: the post decision below and the
            # announcements at the end of this branch both use it.
            send_game = getattr(Item.location, "game", None) or getattr(Item, "game", None)
            handler_ctx = None
            send_event = None
            if get_handler_fuzzy(send_game) is not None:
                handler_ctx = _handler_context_for(Item, timestamp, line_no)
                send_event = SendEvent(
                    ctx=handler_ctx,
                    sender=sender,
                    receiver=receiver,
                    item=Item,
                    location=Item.location,
                    classification=Item.classification,
                    is_filler=Item.is_filler(),
                    is_currency=Item.is_currency(),
                    skip_msg=skip_msg,
                    in_release=sender in release_buffer,
                    in_collect=receiver in collect_buffer,
                )
            handler_decision = dispatch(
                send_game, "should_post_item_send", handler_ctx, send_event, default=None
            )
            if handler_decision is not None:
                if not handler_decision.post:
                    continue
                logger.debug(
                    f"Handler claimed a {Item.classification} send "
                    f"({handler_decision.reason}): {item_location}"
                )
            # original rule
            elif Item.is_filler() or Item.is_currency():
                continue

            # If this is part of a release, send it there instead
            if (
                sender in release_buffer
                and not skip_msg
                and (timestamp - release_buffer[sender]["timestamp"] <= RELEASE_DELTA)
            ):
                release_buffer[sender]["items"][receiver].append(Item)
                logger.debug(f"Adding {item} for {receiver} to release buffer.")
            elif (  # Or if it is part of a collect
                receiver in collect_buffer
                and not skip_msg
                and (timestamp - collect_buffer[receiver]["timestamp"] <= RELEASE_DELTA)
            ):
                collect_buffer[receiver]["items"][sender].append(Item)
                logger.debug(f"{Item.location.name} was collected by {receiver}.")
            else:
                # Update item name based on settings for special items
                location = item_location
                if bool(game.players[receiver].settings):
                    try:
                        item = handle_item_tracking(game, game.players[receiver], Item)
                        location = handle_location_tracking(
                            game, game.players[sender], Item
                        )
                    except KeyError as e:
                        logger.error(
                            f"Couldn't do tracking for item {item} or location {location}:",
                            e,
                            exc_info=True,
                        )

                # Update the message appropriately
                if Item.classification == "trap":
                    trap_messages: list[str] = []

                    def random_nontrap_item(player: Player) -> str:
                        """Get the name of a random non-trap item from the player's spoiler log.
                        Useful for extra flavor in trap messages."""

                        non_trap_items: list[str] = [
                            it.name
                            for it in game.item_instance_cache.values()
                            if it.classification not in ["trap", "currency", "filler"]
                            and it.receiver == player
                            and it.found is False
                        ]

                        if len(non_trap_items) == 0:
                            return "a mysterious item"
                        return str(random.choice(non_trap_items))

                    def trapmsg_substvars(
                        string: str, sender: str, receiver: str, trap: str
                    )  -> str:
                        string = string.replace("$s", sender)
                        string = string.replace("$r", receiver)

                        # Full trap name
                        string = string.replace("$t", trap)
                        # Trap name without the 'Trap' suffix, if it had one
                        string = string.replace("$T", trap.replace(" Trap", ""))

                        # Some random non-trap item from the receiver's spoiler log
                        # Jokes!
                        string = string.replace(
                            "$i", random_nontrap_item(game.players[receiver])
                        )

                        return string

                    if sender == receiver:
                        trap_messages = [
                            "**$s** needed more challenge, and collected **their own $t**",
                            "**$s** thought it was $i, but it was I, **$t**!",
                            "**$s** is a FOOL! (collected a **$t**)",
                            "**$s** was **$T'd!**",
                            "A **$t** destroyed **$s's** world (and everything inside)",
                            "**$s** tested out **their own $t**. It works!",
                            "Let **$s** appreciate you the subtle strategy of taking this **$t**",
                            "**$s**? Is... is that the **$t**...?",
                            "**$s** is here for a **$T** time, not a long time",
                        ]
                    else:
                        trap_messages = [
                            "$s slapped **$r** around a bit with **a large $t**",
                            "**$r**: Congratulations On Your **$t**! Love, $s",
                            "$s, did **$r** *really* deserve that **$t**?",
                            "$s definitely *did not* send **$r** a **$t**",
                            "**$r**, is this a good time for a **$t** from $s?",
                            "**$r** received a demo of what it's like to get a **$t** from $s",
                            "$s destroyed **$r's** world (and everything inside) with a **$t**",
                            "**$r**: Do you have $i? Because $s has **$t**",
                            "$s offered **$r** a **$t** they have no chance of avoiding",
                            "$s innovates **$r's** game - look how our new **$t** changes the experience",
                        ]

                    message = random.choice(trap_messages)
                    message = (
                        dim_if_goaled(receiver)
                        + trapmsg_substvars(
                            message, sender, receiver, item_with_icon(item, icon)
                        )
                        + f" ({location})"
                    )
                    if not skip_msg:
                        message_buffer.append(message.replace("_", r"\_"))
                else:
                    if sender == receiver:
                        message = f"**{sender}** found **their own {
                            'hinted '
                            if bool(game.spoiler_log[sender][item_location].hinted)
                            else ''
                        }{item_with_icon(item, icon)}** ({location})"
                    elif bool(game.spoiler_log[sender][item_location].hinted):
                        message = f"{dim_if_goaled(receiver)}{sender} found **{receiver}'s hinted {item_with_icon(item, icon)}** ({location})"
                    else:
                        message = f"{dim_if_goaled(receiver)}{sender} sent **{item_with_icon(item, icon)}** to **{receiver}** ({location})"
                    if not skip_msg:
                        message_buffer.append(message.replace("_", r"\_"))

                # Last thing this send does: let handlers announce something now that the item has
                # landed - area access, level completion, or (the motivating example) a hinted item
                # becoming checkable. Announcements render in the log channel only; they are not
                # dispatched through the chat handler. Dedupe state lives in each handler's own
                # namespace, so a replayed prefix after a crash does not repeat one.
                if handler_ctx is not None and send_event is not None:
                    for announcement in dispatch_announcements(
                        send_game, handler_ctx, send_event
                    ):
                        logger.info(f"[ANNOUNCE:{announcement.kind}] {announcement.text}")
                        if not skip_msg:
                            message_buffer.append(announcement.text)

                # Handle completion milestones
                # if game.players[sender].collection_percentage == 100 and game.players[sender].is_finished() is False:
                #     message = f"**That was their last check! They're probably just waiting to finish now...**"
                #     message_buffer.append(message)
                # ^ this is the kind of thing that belongs in a handler's on_item_send now.

        elif match := regex_patterns["item_hints"].match(line):
            timestamp = match.groups()[0]
            receiver = match.groups()[1]
            item = match.groups()[2]
            item_location = match.groups()[3]
            sender = match.groups()[4]
            if match.group("entrance"):
                entrance = match.group("entrance")
            else:
                entrance = None
            if match.group("hint_status"):
                hint_status = match.group("hint_status")

            if hint_status == "found":
                continue

            Item = game.get_or_create_item(
                game.players[sender],
                game.players[receiver],
                item,
                item_location,
                entrance=entrance,
            )
            if item_location not in game.spoiler_log[sender]:
                game.spoiler_log[sender][item_location] = Item
            else:
                Item = game.spoiler_log[sender].get(item_location)

            # Store the hint in the player's hints dictionary
            game.players[sender].add_hint("sending", Item)
            game.players[receiver].add_hint("receiving", Item)
            game.spoiler_log[sender][item_location].hint()

            if Item.is_filler() or Item.is_currency():
                continue

            # A game handler may suppress this hint (see cmds/ap_scripts/game_handlers/): a hint
            # fires the moment an item appears in a shop or a pack, usually right before the player
            # buys it. The Balatro exclusion below is the first case of that rule - migrating it to
            # balatro.py would retire it from here.
            if dispatch(
                getattr(Item.location, "game", None) or getattr(Item, "game", None),
                "should_skip_hint",
                _handler_context_for(Item, timestamp, line_no),
                Item,
                default=None,
            ):
                continue
            # Balatro shop items are hinted as soon as they appear and are usually bought right away, so skip their hints
            if Item.location.game == "Balatro" and any(
                [
                    Item.location.name.startswith(shop)
                    for shop in ["Shop Item", "Consumable Item"]
                ]
            ):
                continue

            item_location = handle_location_tracking(
                game, game.players[sender], Item, True
            )

            message = Item.to_hint_text()

            requirements_text = join_words(
                format_requirements(
                    Item.location.requirements, getattr(Item.location, "costs", None)
                )
            )
            if requirements_text:
                message += f"\n> -# This will require {requirements_text} to obtain."
            if bool(Item.location.description):
                message += f"\n> -# {Item.location.description}"

            if (
                not skip_msg
                and game.players[receiver].is_finished() is False
                and not Item.found
            ):
                message_buffer.append(message)
                logger.info(
                    f"[HINT] {sender}: {item_location} -> {receiver}'s {item} ({Item.classification})"
                )

        elif match := regex_patterns["goals"].match(line):
            timestamp, sender = match.groups()
            if sender not in game.players:
                game.players[sender] = {"goaled": True}
            game.players[sender].goaled = True
            game.players[sender].finished_percentage = game.players[
                sender
            ].collection_percentage

            message = f"**{sender} has finished!** That's {len([p for p in game.players.values() if p.is_goaled()])}/{len(game.players)} goaled! ({len([p for p in game.players.values() if p.is_finished()])}/{len(game.players)} including releases)"
            if (
                game.players[sender].collected_locations
                == game.players[sender].total_locations
            ):
                message += (
                    f"\n**Wow!** {sender} 100%ed their game before finishing, too!"
                )
            if not skip_msg:
                logger.info(f"{sender} has finished their game.")
                message_buffer.append(message)
        elif match := regex_patterns["releases"].match(line):
            timestamp, sender = match.groups()
            game.players[sender].released = True
            if not skip_msg:
                logging.info(f"{sender} has released their remaining items.")
                release_buffer[sender] = {
                    "timestamp": parse_to_datetime(timestamp),
                    "items": defaultdict(list),
                }
        elif match := regex_patterns["collects"].match(line):
            timestamp, receiver = match.groups()
            game.players[receiver].collected = True
            if not skip_msg:
                logging.info(f"{receiver} has collected their remaining items.")
                collect_buffer[receiver] = {
                    "timestamp": parse_to_datetime(timestamp),
                    "items": defaultdict(list),
                }
        elif match := regex_patterns["room_shutdown"].match(line):
            game.running = False
            if not skip_msg:
                logger.info("Room has spun down due to inactivity.")
        elif match := regex_patterns["room_spinup"].match(line):
            timestamp, address = match.groups()
            game.running = True
            if not skip_msg:
                logger.info(f"Room has spun up at {address}.")
            if address != seed_address:
                if seed_address is None:
                    seed_address_was = None
                else:
                    seed_address_was = seed_address
                seed_address = address
                logger.info(f"Seed URI has changed: {address}")
                if not skip_msg:
                    with sqlcon.cursor() as cursor:
                        game.pushdb(
                            cursor,
                            "archipelago.ap_all_rooms",
                            "port",
                            seed_address.split(":")[1],
                        )
                        sqlcon.commit()
                    if seed_address_was is not None:
                        message = f"**The seed address has changed.** Use this updated address: `{address}`"
                        send_meta("Archipelago", message)
                        message_buffer.append(message)
            if start_time is None:
                start_time = parse_to_datetime(timestamp)
                if start_time is None:
                    logger.error(
                        f"Failed to parse start time from timestamp: {timestamp}"
                    )
                logger.info(f"Start time set to {start_time} (epoch)")

                # Update "Starting Items" timestamps
                for player in game.players.values():
                    for item in player.inventory:
                        if (
                            item.location.name == "Starting Items"
                            and item.location.player == "Archipelago"
                        ):
                            item.received_timestamp = start_time
        elif match := regex_patterns["messages"].match(line):
            timestamp, sender, message = match.groups()
            if msg_webhooks:
                if message.startswith("!"):
                    continue  # don't send commands
                else:
                    if not skip_msg and sender in game.players:
                        logger.info(f"[CHAT] {sender}: {message}")
                        send_chat(sender, message)

        elif match := regex_patterns["joins"].match(line):
            timestamp, player, verb, playergame, client_version, tags = match.groups()

            timestamp = parse_to_datetime(timestamp)

            try:
                tags_str = tags
                tags = ast.literal_eval(tags_str)
                game.players[player].tags = tags
            except json.JSONDecodeError:
                logger.error(f"Failed to parse player tags. {player}: {tags_str}")
                tags = tags_str
            if not skip_msg and verb == "playing":
                logger.info(f"{player} ({playergame}) is online.")
                game.players[player].set_online(True, timestamp)
            if "Tracker" in tags or verb == "tracking":
                if not skip_msg:
                    # pass
                    logger.info(f"{player} is checking what is in logic.")
                #     message_buffer.append(message)

        elif match := regex_patterns["parts"].match(line):
            timestamp, player, version, tags = match.groups()

            timestamp = parse_to_datetime(timestamp)

            if not skip_msg:
                logger.info(f"{player} is offline.")
            game.players[player].set_online(False, timestamp)

        else:
            # Unmatched lines
            logger.debug(f"Unparsed line: {line}")

        line_end_time = time.time_ns()

        # If the line processing took more than 5 ms, log it

        if line_end_time - line_start_time > 5_000_000:
            logger.debug(
                f"Processing line took {(line_end_time - line_start_time) / 1_000_000} ms: {line}"
            )


### Common non-loop functions


def log_to_file(message):
    global room_id

    os.makedirs("logs", exist_ok=True)  # Ensure logs directory exists
    with open(f"logs/{room_id}.md", "a", encoding="UTF-8") as log_file:
        log_file.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")


def send_chat(sender, message):
    payload = {"username": sender, "content": message}

    for webhook in msg_webhooks:
        if webhook is None or webhook == "":
            continue
        try:
            if "discord.com" not in webhook:
                payload["content"] = re.sub(r"<:\S+:\d+>", "", payload["content"])
            if "/slack" in webhook:
                payload["text"] = payload["content"]
                del payload["content"]
            response = requests.post(webhook, json=payload, timeout=5)
            response.raise_for_status()
            # log_to_file(message)  # Log the message to a file
        except requests.RequestException as e:
            logging.error(f"Error sending chat message to webhook: {e}")


def send_log(message):
    payload = {"content": message}

    for webhook in webhook_urls:
        if webhook is None or webhook == "":
            continue
        try:
            if "/slack" in webhook:
                payload["text"] = payload["content"]
                payload["text"] = payload["text"].replace("<:progression:1424290927735869461>", ":archipelago_progression:")
                payload["text"] = payload["text"].replace("<:trapitem:1450760161286295734>", ":archipelago_trap:")
                payload["text"] = payload["text"].replace("<:filler:1461179676365164729>", ":archipelago_filler:")
                payload["text"] = payload["text"].replace("<:coin:1469865094665207879>", ":coin:")
                payload["text"] = payload["text"].replace("<:unclassified:1450498207032283357>", ":archipelago_unclassified:")
                del payload["content"]
            elif "discord.com" not in webhook:
                payload["content"] = re.sub(r"<:\S+:\d+>", "", payload["content"])
            response = requests.post(webhook, json=payload, timeout=5)
            response.raise_for_status()
            # log_to_file(message)  # Log the message to a file
        except requests.RequestException as e:
            logging.error(f"Error sending log message to webhook: {e}")


def send_meta(sender, message):
    payload = {"username": sender, "content": message}

    try:
        response = requests.post(meta_webhook[0], json=payload, timeout=5)
        response.raise_for_status()
        # log_to_file(message)  # Log the message to a file
    except requests.RequestException as e:
        logging.error(f"Error sending meta message to webhook: {e}")


def send_release_messages():
    global release_buffer

    for sender, data in release_buffer.copy().items():
        if len(data) == 0:
            continue
        if time.time() - data["timestamp"].timestamp() > 1:
            message = f"**{sender}** has released their remaining items."
            initial_msg = message
            running_message = message
            for receiver, items in data["items"].items():
                if game.players[receiver].is_finished():
                    continue
                item_counts = defaultdict(int)
                for item in items:
                    if item.is_filler():
                        continue
                    item_counts[item.name] += 1
                item_list = ", ".join(
                    [
                        f"{item} (x{count})" if count > 1 else item
                        for item, count in item_counts.items()
                    ]
                )
                # A receiver whose whole share was filler/currency has nothing to list;
                # an empty "receives:" line is noise, so skip that receiver entirely.
                if not item_counts:
                    continue
                running_message += (
                    f"\n{dim_if_goaled(receiver)}**{receiver}** receives: {item_list}"
                )
                if len(running_message) > MAX_MSG_LENGTH:
                    send_log(message)
                    message = running_message.replace(message, "")
                    time.sleep(1)
                else:
                    message = running_message
            if message == initial_msg:
                # Nothing summarisable went to a player who still needs it (all filler, or
                # every receiver already finished). Drop the entry: it used to `return`
                # here, which abandoned the whole buffer - one empty release then blocked
                # every later release summary for the rest of the seed.
                del release_buffer[sender]
                continue
            else:
                send_log(message)
                logger.info(f"{sender} release sent.")
            del release_buffer[sender]


def send_collection_messages():
    global collect_buffer

    for receiver, data in collect_buffer.copy().items():
        if len(data) == 0:
            continue
        if time.time() - data["timestamp"].timestamp() > 1:
            message = f"**{receiver}** has collected their items from the multiworld."
            initial_msg = message
            running_message = message
            for sender, items in data["items"].items():
                if game.players[sender].is_finished():
                    continue
                loc_list = ", ".join([i.location.name for i in items])
                running_message += f"\n{dim_if_goaled(sender)}**{sender}**, {len(items)} locations collected: {loc_list}"
                if len(running_message) > MAX_MSG_LENGTH:
                    send_log(message)
                    message = running_message.replace(message, "")
                    time.sleep(1)
                else:
                    message = running_message
            if message == initial_msg:
                # Same deadlock as send_release_messages(): drop the entry instead of
                # abandoning every sender still queued behind it.
                del collect_buffer[receiver]
                continue
            else:
                send_log(message)
                logger.info(f"{receiver} collection sent.")
            del collect_buffer[receiver]


def fetch_log(url):
    try:
        cookies = {"session": session_cookie}
        response = requests.get(url, cookies=cookies, timeout=15)
        response.raise_for_status()
        return response.text.splitlines()
    except requests.RequestException as e:
        logger.error(f"Error fetching log file: {e}")
        return False


### Emitter events


def handle_milestone_message(message):
    message_buffer.append(message)
def handle_sphere_message(message):
    message_buffer.append(message)

event_emitter.on("milestone", handle_milestone_message)
# event_emitter.on("sphere_completion", handle_sphere_message)

### Main function to watch the log file


def watch_log(url, interval):
    global collect_buffer
    global release_buffer
    global players
    global game

    last_line = 0

    game.fetch_room_api()
    game.fetch_static_tracker()
    game.fetch_slot_data()

    if seed_url:
        logger.info("Processing spoiler log.")
        game.has_spoiler = True
        process_spoiler_log(seed_url)
        for player in game.players.values():
            # We're going to 'collapse' player spheres here:
            # If any player has *no* items in a sphere, delete that sphere
            # That way we can still have accurate sphere counts and milestones without
            # worrying about players having different sphere distributions
            player.collapse_spheres()

    if pathlib.Path(f".cache/{room_id}").exists():
        for player in game.players.values():
            if pathlib.Path(f".cache/{room_id}/{player.name}.json").exists():
                logger.info(f"Restoring uploaded data for {player.name}...")
                with open(f".cache/{room_id}/{player.name}.json") as file:
                    player.upload_data = json.loads(file.read())

    previous_lines = fetch_log(url)

    if not previous_lines:
        # Sleep and try again until we get something
        while not previous_lines:
            time.sleep(15)
            previous_lines = fetch_log(url)

    logger.info("Parsing existing log lines before we start watching it...")

    # Get the last line number we processed from the database
    if not DEBUG:
        with sqlcon.cursor() as cursor:
            try:
                last_line = int(game.pulldb(cursor, "archipelago.ap_all_rooms", "last_line"))
            except TypeError:
                # Last Line probably hasn't been set yet; this room is new
                pass

    process_new_log_lines(previous_lines[:last_line], True, line_offset=0)  # Read for hints etc

    if pathlib.Path(f".cache/{room_id}").exists() and pathlib.Path(f".cache/{room_id}/spoiled_items.json").exists():
        with open(f".cache/{room_id}/spoiled_items.json", "r") as file:
                    for line in file:
                        # Parse "PlayerName`ItemName`LocationName" into a dict with keys "player", "item", and "location"
                        try:
                            receiver, item, sender, location = line.strip().split("`")
                            game.spoiler_log[sender][location].spoiled = True
                        except ValueError:
                            logger.error(f"Failed to parse spoiled item line: {line.strip()}")
    release_buffer = {}
    collect_buffer = {}
    logger.info(f"Initial log lines: {len(previous_lines[:last_line])}")
    logger.info(
        f"Log lines queued up for processing: {len(previous_lines[last_line:])}"
    )
    for p in game.players.values():
        p.update_locations(game)
        p.on_item_collected(None)
    game.update_locations()
    logger.info(f"Total Checks: {game.total_locations}")
    logger.info(f"Checks Collected: {game.collected_locations}")
    logger.info(f"Completion Percentage: {round(game.collection_percentage, 2)}%")
    logger.info(f"Total Players: {len(game.players)}")
    logger.info(f"Seed Address: {seed_address}")
    logger.info(f"Logging messages to {len(webhook_urls)} webhook(s).")
    logger.info(f"Logging chats to {len(msg_webhooks)} webhook(s).")

    if not DEBUG:
        with sqlcon.cursor() as cursor:
            try:
                game.pushdb(
                    cursor, "archipelago.ap_all_rooms", "port", seed_address.split(":")[1]
                )
                sqlcon.commit()
            except AttributeError:
                # Seed Address not processed/set yet
                pass

    message_buffer.clear()  # Clear buffer in case we have any old messages

    # classification_thread = threading.Thread(target=save_classifications)
    # classification_thread.start()

    logger.info("Ready!")
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()

    tracker_sleep_count = 10

    ### Main Loop
    while True:
        if tracker_sleep_count >= 10 and game.running is False:
            game.fetch_tracker()
            tracker_sleep_count = 0
        time.sleep(interval)
        current_lines = fetch_log(url)
        if current_lines is False:
            # if fetch fails we don't want it to sync back '0' and then re-read the entire log file
            pass
        elif len(current_lines) > last_line:
            new_lines = current_lines[last_line:]
            if len(new_lines) > 0:
                process_new_log_lines(new_lines, line_offset=last_line)
                tracker_sleep_count += 1
            if message_buffer:
                try:
                    # Join all messages with newlines
                    all_messages = "\n".join(message_buffer)
                    if len(all_messages) > MAX_MSG_LENGTH:
                        logger.warning(
                            f"Message buffer exceeded {MAX_MSG_LENGTH} characters, splitting into chunks."
                        )
                        # Split into chunks not exceeding MAX_MSG_LENGTH
                        chunks = []
                        current_chunk = ""
                        for msg in message_buffer:
                            # +1 for the newline if not first message
                            if (
                                len(current_chunk)
                                + len(msg)
                                + (1 if current_chunk else 0)
                                > MAX_MSG_LENGTH
                            ):
                                if current_chunk:
                                    chunks.append(current_chunk)
                                current_chunk = msg
                            else:
                                if current_chunk:
                                    current_chunk += "\n" + msg
                                else:
                                    current_chunk = msg
                        if current_chunk:
                            chunks.append(current_chunk)
                        # Send each chunk, waiting 2 seconds between
                        for i, chunk in enumerate(chunks):
                            send_log(chunk)
                            logger.debug(
                                f"sent chunk {i + 1}/{len(chunks)} ({len(chunk)} chars) to webhook"
                            )
                            if i < len(chunks) - 1:
                                time.sleep(2)
                    else:
                        send_log(all_messages)
                        logger.debug(
                            f"sent {len(message_buffer)} messages ({len(all_messages)} chars) to webhook"
                        )

                    # Clear the buffer and sync last_line if successful
                    message_buffer.clear()
                    if len(current_lines) > last_line:
                        last_line = len(current_lines)
                        with sqlcon.cursor() as cursor:
                            game.pushdb(
                                cursor,
                                "archipelago.ap_all_rooms",
                                "last_line",
                                len(current_lines),
                            )
                            sqlcon.commit()
                except requests.RequestException as e:
                    pass

        if len(release_buffer) > 0:
            if any(
                datetime.now(ZoneInfo("UTC")).astimezone()
                - release_buffer[sender]["timestamp"]
                > RELEASE_DELTA
                for sender in release_buffer.keys()
            ):
                logger.info(f"Release buffer period has already passed, sending.")
                send_release_messages()

        if len(collect_buffer) > 0:
            if any(
                datetime.now(ZoneInfo("UTC")).astimezone()
                - collect_buffer[sender]["timestamp"]
                > RELEASE_DELTA
                for sender in collect_buffer.keys()
            ):
                logger.info(f"Collect buffer period has already passed, sending.")
                send_collection_messages()

        if (
            len(message_buffer) == 0
            and bool(current_lines)
            and len(current_lines) > last_line
        ):
            # If we have no messages to send but the log has updated, sync last_line anyway
            last_line = len(current_lines)

        # Check if all players have finished
        if (
            all(p.is_finished() for p in game.players.values())
            and len(message_buffer) == 0
            and len(release_buffer) == 0
            and len(collect_buffer) == 0
        ):
            logger.info(
                "All players have finished and are offline, and there's no more messages in the buffers to process. We're done here."
            )

            # Some maintenance items before we exit
            for p in game.players.values():
                if p.released is True:
                    # Any locations not 'checked' by this point should be marked as uncheckable
                    logger.debug(
                        f"{p.name} ({p.game}) released, marking remaining unchecked locations as uncheckable."
                    )
                    for loc in p.spoilers["locations"].values():
                        location = loc.location
                        if location.found is False and location.is_checkable is None:
                            logger.info(
                                f"Marking {p.game}: {location.name} as uncheckable."
                            )
                            location.db_add_location(is_check=False)

            # We're done
            logger.info("Sleeping forever now. (Keeping the API open) Goodnight!")
            while True:
                time.sleep(600)
        logger.debug(f"Message buffer has {len(message_buffer)} messages queued.")


def process_releases():
    global release_buffer
    logger.info("Watching for releases.")

    while True:
        time.sleep(10)
        while len(release_buffer) > 0:
            time.sleep(INTERVAL)
            send_release_messages()


def process_collects():
    global collect_buffer
    logger.info("Watching for collects.")

    while True:
        time.sleep(10)
        while len(collect_buffer) > 0:
            time.sleep(INTERVAL)
            send_collection_messages()


# Flask stuff
webview = Flask(__name__)
webview.logger.removeHandler(default_handler)
CORS(webview)


def safe_globals():
    # Only show non-private, non-module, non-callable globals
    return {
        k: repr(v)
        for k, v in globals().items()
        if not k.startswith("__")
        and not callable(v)
        and not isinstance(v, type(webview))
        and k not in ("webview", "request", "Response")
    }


@webview.route("/inspect", methods=["GET"])
def inspect():
    # For easier reading, return as plain text
    import pprint

    return Response(pprint.pformat(safe_globals()), mimetype="text/plain")


@webview.route("/inspectgame", methods=["GET"])
def get_game():
    return jsonify(game.to_dict())

@webview.route("/spoilitem/<player_name>/<item>", methods=["GET"])
def spoil_item(player_name: str, item: str):
    """Reveal the location of a specific item in a player's spoiler log.
    Marks the item as 'spoiled'."""
    player = game.get_player(player_name)
    if player is None:
        return jsonify({"error": f"Player '{player_name}' not found"}), 404

    for item_location, item_obj in game.spoiler_log[player_name].items():
        if item_obj.name == item:
            item_obj.spoiled = True

            if not pathlib.Path(f".cache/{room_id}").exists():
                pathlib.Path(f".cache/{room_id}").mkdir(parents=True, exist_ok=True)
            with open(f".cache/{room_id}/spoiled_items.json", "a") as file:
                file.write(f"{player_name}`{item}`{item_obj.location.player}`{item_location}\n")

            return jsonify({
                "player": player_name,
                "item": item,
                "location": item_location,
                "hint_text": item_obj.to_hint_text(),
            }), 200

    return jsonify({"error": f"Item '{item}' not found in {player_name}'s spoiler log"}), 404


@webview.route("/refreshclassifications", methods=["GET"])
def refresh_classifications():
    global game

    # Optional query parameters to narrow the refresh scope
    game_name = request.args.get("game")
    item_name = request.args.get("item")

    try:
        processed, updated = game.refresh_classifications(
            game=game_name, item_name=item_name
        )
        msg = f"Refreshed classifications. Processed={processed}, Updated={updated}."
        if game_name:
            msg = f"Refreshed classifications for game='{game_name}'{f", item='{item_name}'" if item_name else ''}. Processed={processed}, Updated={updated}."
        return msg, 200
    except Exception as e:
        logger.exception(f"Failed to refresh classifications: {e}")
        return f"Error refreshing classifications: {e}", 500


@webview.route("/locations/checkable/", methods=["GET"], defaults={"found": False})
@webview.route("/locations/checkable/found", methods=["GET"], defaults={"found": True})
def get_checkable_locations(found: bool = False):
    locationtable = {}
    for player_name, player in game.players.items():
        if player.game not in locationtable:
            locationtable[player.game] = {}
        for location_name, location in player.locations.items():
            if found:
                locationtable[player.game][location_name] = [
                    location.found,
                    location.is_location_checkable,
                ]
            else:
                locationtable[player.game][location_name] = (
                    location.is_location_checkable
                )
    return jsonify(locationtable)


@webview.route("/upload_data/<slotname>", methods=["POST"])
def upload_data(slotname: str):
    player = None
    logger.debug(f"Available players: {list(game.players.keys())}")
    logger.debug(f"Player names: {[p.name for p in game.players.values()]}")
    logger.debug(f"Received slotname bytes: {slotname.encode('utf-8')}")
    logger.debug(f"Received slotname repr: {repr(slotname)}")

    player = game.get_player(slotname)

    if player is None:
        logger.error(f"Couldn't find player '{slotname}' to upload data to")
        logger.error(f"Available: {[p.name for p in game.players.values()]}")
        return jsonify({"error": f"Player {slotname} not found"}), 404

    try:
        data = request.get_json()
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid JSON format, expected a dictionary"}), 400

        player.upload_data = data

        if not pathlib.Path(f".cache/{room_id}").exists():
            pathlib.Path(f".cache/{room_id}").mkdir(parents=True, exist_ok=True)
        with open(f".cache/{room_id}/{player.name}.json", "w") as file:
            file.write(json.dumps(data))

        return jsonify(
            {"message": f"Data uploaded successfully for player {slotname}"}
        ), 200
    except Exception as e:
        logger.error(f"Error uploading data for player {slotname}: {e}")
        return jsonify({"error": str(e)}), 500

def create_progress_bar(percentage: float, width: int = 20) -> str:
    """Create a text-based progress bar representation.
    
    Args:
        percentage: Progress as a percentage (0-100)
        width: Width of the progress bar in characters
    
    Returns:
        A string representation of the progress bar
    """
    filled = int((percentage / 100) * width)
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {percentage:.1f}%"


@webview.route("/progress", methods=["GET"])
def get_progress():
    """Get overall multiworld progress for all players."""
    try:
        progress_data = {
            "total_percentage": game.collection_percentage,
            "collected_locations": game.collected_locations,
            "total_locations": game.total_locations,
            "progress_bar": create_progress_bar(game.collection_percentage),
            "players": {}
        }
        
        for player_name, player in game.players.items():
            progress_data["players"][player_name] = {
                "game": player.game,
                "percentage": player.collection_percentage,
                "collected_locations": player.collected_locations,
                "total_locations": player.total_locations,
                "progress_bar": create_progress_bar(player.collection_percentage),
                "is_finished": player.is_finished(),
                "goaled": player.goaled,
                "released": player.released,
                "collected": player.collected
            }
        
        return jsonify(progress_data), 200
    except Exception as e:
        logger.error(f"Error getting progress: {e}")
        return jsonify({"error": str(e)}), 500


@webview.route("/progress/<player_name>", methods=["GET"])
def get_player_progress(player_name: str):
    """Get progress for a specific player."""
    try:
        player = game.get_player(player_name)
        
        if player is None:
            return jsonify({"error": f"Player '{player_name}' not found"}), 404
        
        progress_data = {
            "player_name": player.name,
            "game": player.game,
            "percentage": player.collection_percentage,
            "collected_locations": player.collected_locations,
            "total_locations": player.total_locations,
            "progress_bar": create_progress_bar(player.collection_percentage),
            "is_finished": player.is_finished(),
            "goaled": player.goaled,
            "released": player.released,
            "collected": player.collected
        }
        
        return jsonify(progress_data), 200
    except Exception as e:
        logger.error(f"Error getting progress for player {player_name}: {e}")
        return jsonify({"error": str(e)}), 500

def run_flask():
    # Dynamically select an available port starting from 42069
    port = 42069
    while True:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("0.0.0.0", port))
                s.close()

                # Check if the port is already in use by another seed in the database
                if sqlcon:
                    with sqlcon.cursor() as cursor:
                        cursor.execute(
                            "SELECT COUNT(*) FROM archipelago.ap_all_rooms WHERE flask_port = %s AND room_id != %s AND active = 'true'",
                            (port, room_id),
                        )
                        if cursor.fetchone()[0] == 0:
                            pass
                        else:
                            raise ValueError(
                                f"Port {port} is already in use by another seed in the database."
                            )
                break
            except OSError:
                port += 1
            except ValueError:
                port += 1

    logger.info(f"Starting Flask webview on port {port}...")

    # Store the selected port in the database for use elsewhere
    if sqlcon:
        with sqlcon.cursor() as cursor:
            game.pushdb(cursor, "archipelago.ap_all_rooms", "flask_port", port)
            sqlcon.commit()
    webview.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)


if __name__ == "__main__":
    logger.info(f"logging messages from AP Room ID {room_id}")

    release_thread = threading.Thread(target=process_releases)
    release_thread.start()

    collect_thread = threading.Thread(target=process_collects)
    collect_thread.start()

    watch_log(log_url, INTERVAL)

import datetime
import time
import re
import psycopg2 as psql
import logging
import yaml
import discord

from typing import Iterable, Any

from cmds.ap_scripts.emitter import event_emitter
from zoneinfo import ZoneInfo

# setup logging
logger = logging.getLogger('ap_itemlog')

with open('config.yaml', 'r', encoding='UTF-8') as file:
    cfg = yaml.safe_load(file)


sqlcfg = cfg['bot']['archipelago']['psql']
try:
    sqlcon = psql.connect(
        dbname=sqlcfg['database'],
        user=sqlcfg['user'],
        password=sqlcfg['password'] if 'password' in sqlcfg else None,
        host=sqlcfg['host'],
        port=sqlcfg['port']
    )
except psql.OperationalError:
    # TODO Disable commands that need SQL connectivity
    sqlcon = False


classification_cache = {}
cache_timeout = 1*60*60 # 1 hour(s)

item_table = {}

# def push_to_database(cursor: psql.cursor, game: Game, database: str, column: str, payload):
#     try:
#             cursor.execute(f"UPDATE {database} set {column} = %s WHERE room_id = %s", (payload, room_id))
#     except Exception as e:
#         logger.error(f"Error pushing to database: {e}")


class Game(dict):
    seed = None
    room_id = None
    version_generator = None
    version_server = None
    running: bool = False
    world_settings = {}
    spoiler_log = {}
    players = {}
    collected_locations: int = 0
    total_locations: int = 0
    collection_percentage: float = 0.0
    milestones = set()
    start_timestamp: float = None

    # This is a cache for Item instances, so we don't have to create new ones every time
    # Unique by (sender, location)
    # Store it in the Game class to keep duplicate instances minimal
    item_instance_cache = {}

    def init_db(self):
        cursor = sqlcon.cursor()

        # DBs to do:
        # {room_id}
        # {room_id}_locations
        # {room_id}_items
        # {room_id}_p_{player}

        cursor.execute(
            f"CREATE TABLE IF NOT EXISTS games.{self.room_id} (setting bpchar, value bpchar, classification varchar(32))")

    def update_locations(self):
        self.collected_locations = sum([p.collected_locations for p in self.players.values()])
        self.total_locations = sum([p.total_locations for p in self.players.values()])
        self.collection_percentage = (self.collected_locations / self.total_locations) * 100 if self.total_locations > 0 else 0.0

        self.check_milestones()

    def check_milestones(self):
        milestones = [25, 50, 75, 100]  # Define milestones
        for milestone in milestones:
            if self.collection_percentage >= milestone and milestone not in self.milestones:
                self.milestones.add(milestone)
                logger.info(f"Game reached {milestone}% completion!")
                message = f"**The game has reached {milestone}% completion!**"
                event_emitter.emit("milestone", message)  # Emit the milestone message

    def to_dict(self):
        return {
            "seed": self.seed,
            "room_id": self.room_id,
            "version_generator": self.version_generator,
            "version_server": self.version_server,
            "world_settings": self.world_settings,
            "start_timestamp": self.start_timestamp,
            "running": self.running,
            "spoiler_log": {k: {lk: lv.to_dict() for lk, lv in v.items()} for k, v in self.spoiler_log.items()},
            "players": {k: v.to_dict() for k, v in self.players.items()},
            "collected_locations": self.collected_locations,
            "total_locations": self.total_locations,
            "collection_percentage": self.collection_percentage,
        }
    
    def get_or_create_item(self, sender, receiver, itemname, location, entrance=None, received_timestamp: float = None):
        key = (str(sender), location, itemname)
        if key in self.item_instance_cache:
            item = self.item_instance_cache[key]
            if received_timestamp is not None:
                item.received_timestamp = received_timestamp
            return item
        obj = Item(sender, receiver, itemname, location, entrance, received_timestamp)
        self.item_instance_cache[key] = obj
        return obj

    def pushdb(self, cursor, database: str, column: str, payload):
        try:
            cursor.execute(f"UPDATE {database} set {column} = %s WHERE room_id = %s", (payload, self.room_id))
        except Exception as e:
            logger.error(f"Error pushing to database: {e}")

def handle_hint_update(self):
    pass


class Player(dict):
    name = None
    game = None
    inventory: list = []
    locations = {}
    hints = {}
    online = False
    last_online = None
    tags = []
    settings = None
    goaled = False
    released = False
    collected_locations: int = 0
    total_locations: int = 0
    collection_percentage: float = 0.0

    def __init__(self,name,game):
        self.name = name
        self.game = game
        self.inventory = []
        self.locations = {}
        self.hints = {
            "sending": [],
            "receiving": []
        }
        self.settings = PlayerSettings()
        self.goaled = False
        self.released = False
        self.milestones = set()

    def __str__(self):
        return self.name

    def to_dict(self):
        return {
            "name": self.name,
            "game": self.game,
            "inventory": [i.to_dict() for i in self.inventory],
            "locations": {k: v.to_dict() for k, v in self.locations.items()},
            "hints": {k: [i.to_dict() for i in v] for k, v in self.hints.items()},
            "online": self.online,
            "last_online": self.last_online,
            "tags": self.tags,
            "settings": dict(self.settings) if self.settings else {},
            "goaled": self.goaled,
            "released": self.released,
            "collected_locations": self.collected_locations,
            "total_locations": self.total_locations,
            "collection_percentage": self.collection_percentage,
        }

    def is_finished(self) -> bool:
        return self.goaled or self.released

    def is_goaled(self) -> bool:
        return self.goaled

    def set_online(self, online: bool, timestamp: str):
        self.online = online
        self.last_online = time.mktime(datetime.datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f").timetuple())

    def last_seen(self):
        if self.online is True:
            return time.time()
        else:
            return self.last_online

    def update_locations(self, game: Game):
        self.locations = {l.location: l for l in game.spoiler_log[self.name].values()}
        self.total_locations = len([l for l in self.locations.values() if l.is_location_checkable is True])
        if not (self.goaled or self.released):
            self.collected_locations = len([l for l in self.locations.values() if l.found is True])
            self.collection_percentage = (self.collected_locations / self.total_locations) * 100 if self.total_locations > 0 else 0.0

        self.check_milestones()

    def check_milestones(self):
        milestones = [50, 75, 100]  # Define milestones
        if self.goaled or self.released:
            return # Don't process milestones if the player is already finished
        for milestone in milestones:
            if self.collection_percentage >= milestone and milestone not in self.milestones:
                self.milestones.add(milestone)
                message = f"**{self.name} has reached {milestone}% completion!**"
                event_emitter.emit("milestone", message)  # Emit the milestone message

    def add_hint(self, hint_type: str, item):
        if hint_type not in self.hints:
            self.hints[hint_type] = list()
        self.hints[hint_type].append(item)
        self.on_hints_updated()

    def on_hints_updated(self):
        # This method will be called whenever hints are updated
        logger.info(f"Hints for player {self.name} have been updated.")
        handle_hint_update(self)

    def get_item_count(self, item_name: str) -> int:
        """Get the count of a specific item in the player's inventory."""
        return sum(1 for item in self.inventory if item.name == item_name)
    
    def get_collected_items(self, items: Iterable[Any]) -> list:
        """For a list of items requested, return the items that are present in the inventory."""
        collected_items = []

        for collected_item in self.inventory:
            if collected_item.name in items:
                collected_items.append(collected_item)
        
        return collected_items

class Item(dict):
    """An Archipelago item in the multiworld"""

    sender = None
    receiver = None
    name = None
    game = None
    location = None
    location_costs: list[str] = []
    location_info: str = None
    location_entrance = None
    is_location_checkable = None
    classification = None
    count = 1
    found = False
    hinted = False
    spoiled = False
    received_timestamp: float = None

    def __init__(self, sender: Player|str, receiver: Player, item: str, location: str, entrance: str = None, received_timestamp: float = None):
        self.sender = sender
        self.receiver = receiver
        self.name = item
        self.game = receiver.game
        self.location = location
        self.is_location_checkable = self.get_location_checkable()
        self.location_entrance = entrance
        self.location_costs, self.location_info = handle_location_hinting(self.receiver, self)
        self.classification = self.set_item_classification(self)
        self.count: int = 1
        self.found = False
        self.hinted = False
        self.spoiled = False
        self.received_timestamp = received_timestamp

        if self.game is None:
            logger.warning(f"Item object for {self.name} has no game associated with it?")

        if self.game not in item_table:
            item_table[self.game] = {}
        if self.name not in item_table[self.game]:
            item_table[self.game][self.name] = self

    def __str__(self):
        return self.name

    def to_dict(self):
        return {
            "sender": str(self.sender) if hasattr(self.sender, 'name') else self.sender,
            "receiver": str(self.receiver) if hasattr(self.receiver, 'name') else self.receiver,
            "name": self.name,
            "game": self.game,
            "location": self.location,
            "location_entrance": self.location_entrance,
            "location_costs": self.location_costs,
            "location_info": self.location_info,
            "is_location_checkable": self.is_location_checkable,
            "classification": self.classification,
            "count": self.count,
            "found": self.found,
            "hinted": self.hinted,
            "spoiled": self.spoiled,
            "received_timestamp": self.received_timestamp
        }

    def collect(self):
        """Mark this item as collected and add it to the receiver's inventory."""
        self.found = True
        self.receiver.inventory.append(self)

    def hint(self):
        self.hinted = True

    def spoil(self):
        self.spoiled = True

    def get_location_checkable(self) -> bool:
        if not isinstance(self.sender, Player):
            return False # Archipelago starting items, etc
        match self.game:
            case "SlotLock"|"APBingo":
                return True # Metagames are always checkable
            case "Jigsaw"|"Simon Tatham's Portable Puzzle Collection":
                return True # AP-specific games are simple enough that all their locations are checkable
            case "gzDoom":
                # Locations are dynamically generated by the selected wad
                # So let's assume they they are all checkable
                return True 
            case _:
                with sqlcon.cursor() as cursor:
                    cursor.execute("SELECT is_checkable FROM archipelago.game_locations WHERE game = %s AND location = %s;", (self.sender.game, self.location))
                    response = cursor.fetchone()
                    # logger.info(f"locationsdb: {self.sender.game}: {self.location} is checkable: {response[0]}") # debugging in info, yes i know
                    return response[0] if response else False

    def db_add_location(self, is_check: bool = False):
        """Add this item's location to the database if it doesn't already exist.
        If a location shows up in a playthrough, it is a checkable location.
        If it doesn't (only appears in spoiler log), it is *likely* an event.

        If the location already exists, but the 'checkable' value is wrong,
        this function will update the value in the database.

        This should help to establish accurate location counts when we start tracking those."""
        cursor = sqlcon.cursor()

        cursor.execute("CREATE TABLE IF NOT EXISTS archipelago.game_locations (game bpchar, location bpchar, is_checkable boolean)")

        is_checkable: bool = None

        try:
            cursor.execute("SELECT * FROM archipelago.game_locations WHERE game = %s AND location = %s;", (self.sender.game, self.location))
            game, location, is_checkable = cursor.fetchone()
            if is_checkable != is_check and is_check == True:
                logger.debug(f"Request to update checkable status for {self.sender.game}: {self.location} (to: {str(is_check)})")
                cursor.execute("UPDATE archipelago.game_locations set is_checkable = %s WHERE game = %s AND location = %s;", (str(is_check), game, location))
        except TypeError:
            logger.debug("Nothing found for this location, likely")
            logger.info(f"locationsdb: adding {self.sender.game}: {self.location} to the db")
            cursor.execute("INSERT INTO archipelago.game_locations VALUES (%s, %s, %s)", (self.sender.game, self.location, str(is_check)))
        finally:
            sqlcon.commit()
        logger.debug(f"locationsdb: classified {self.sender.game}: {self.location} as {is_checkable}")
        self.is_location_checkable = self.get_location_checkable()


    def set_item_classification(self, player: Player = None):
        """Refer to the itemdb and see whether the provided Item has a classification.
        If it doesn't, creates a new entry for that item with no classification.

        We can pass this through an intermediate step to assume things about
        some common items, but not everything.
        """

        permitted_values = [
            "progression", # Unlocks new checks
            "conditional progression", # Progression overall, but maybe only in certain settings or certain qualities
            "useful", # Good to have but doesn't unlock anything new
            "currency", # Filler, but specifically currency
            "filler", # Filler - not really necessary
            "trap" # Negative effect upon the player
            ]
        response = None # What we will ultimately return

        player = self.receiver if player is None else player

        if self.game is None:
            return None

        if self.game in classification_cache and self.name in classification_cache[self.game]:
            if bool(classification_cache[self.game][self.name][1]) and (time.time() - classification_cache[self.game][self.name][1] > cache_timeout):
                if classification_cache[self.game][self.name][0] is None:
                    logger.warning(f"Invalidating cache for {self.game}: {self.name}")
                    del classification_cache[self.game][self.name]
            else:
                classification = classification_cache[self.game][self.name][0]
                if classification != "conditional progression": return classification

        # Some games are 'simple' enough that everything (or near everything) is progression
        match self.game:
            case "Simon Tatham's Portable Puzzle Collection":
                if self.name == "Filler": response = "filler"
                else: response = "progression"
            case "SlotLock"|"APBingo": response = "progression" # metagames are generally always progression
            case _:
                cursor = sqlcon.cursor()

                cursor.execute("CREATE TABLE IF NOT EXISTS archipelago.item_classifications (game bpchar, item bpchar, classification varchar(32))")

                try:
                    cursor.execute("SELECT classification FROM archipelago.item_classifications WHERE game = %s AND item = %s;", (self.game, self.name))
                    response = cursor.fetchone()[0]
                except TypeError:
                    logger.debug("Nothing found for this item, likely")
                    logger.info(f"itemsdb: adding {self.game}: {self.name} to the db")
                    cursor.execute("INSERT INTO archipelago.item_classifications VALUES (%s, %s, %s)", (self.game, self.name, None))
                finally:
                    sqlcon.commit()
        logger.debug(f"itemsdb: classified {self.game}: {self.name} as {response}")
        if self.game not in classification_cache:
            classification_cache[self.game] = {}
        classification_cache[self.game][self.name] = (response.lower(), time.time()) if bool(response) else (None, time.time())
        return classification_cache[self.game][self.name][0]
        # return response


    def update_item_classification(self, classification: str) -> bool:
        # Abort if already set
        if classification == self.classification: return True

        permitted_values = [
            "progression", # Unlocks new checks
            "conditional progression", # Progression overall, but maybe only in certain settings or certain qualities
            "useful", # Good to have but doesn't unlock anything new
            "currency", # Filler, but specifically currency
            "filler", # Filler - not really necessary
            "trap" # Negative effect upon the player
            ]
        if classification not in permitted_values:
            logger.error(f"Tried to update classification for {self.game}: {self.name} (value '{classification}' not permitted)")
            return False

        logger.info(f"Request to update classification for {self.game}: {self.name} (to: {classification})")
        cursor = sqlcon.cursor()

        cursor.execute("CREATE TABLE IF NOT EXISTS archipelago.item_classifications (game bpchar, item bpchar, classification varchar(32))")

        try:
            cursor.execute("UPDATE archipelago.item_classifications set classification = %s where game = %s and item = %s;", (classification, self.game, self.name))
        finally:
            sqlcon.commit()
            self.set_item_classification(self.receiver)
        return True

    def is_found(self):
        return self.found


    def is_filler(self):
        return self.classification == "filler"
    def is_currency(self):
        return self.classification == "currency"

class PlayerSettings(dict):
    def __init__(self):
        pass

class APEvent:
    def __init__(self, event_type: str, timestamp: str, sender: str, receiver: str = None, location: str = None, item: str = None, extra: str = None):
        self.type = event_type
        self.sender = sender
        self.receiver = receiver if receiver else None
        self.location = location if location else None
        self.item = item if item else None
        self.extra = extra if extra else None

        try:
            self.timestamp = time.mktime(datetime.datetime(tzinfo=ZoneInfo()).strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f"))
        except ValueError as e:
            raise

        match self.type:
            case "item_send"|"hint":
                if not all([bool(criteria) for criteria in [self.sender, self.receiver, self.location, self.item]]):
                    raise ValueError(f"Invalid {self.type} event! Requires a sender, receiver, location, and item.")

def handle_item_tracking(game: Game, player: Player, item: Item):
    """If an item is an important collectable of some kind, we should put some extra info in the item name for the logs."""
    global item_table

    ItemObject = item
    item = item.name

    if bool(player.settings):
        settings = player.settings
        game = player.game
        count = player.get_item_count(item)

        try:
            match game:
                case "A Link to the Past":
                    if item == "Triforce Piece" and "Triforce Hunt" in settings['Goal']:
                        required = settings['Triforce Pieces Required']
                        return f"{item} (*{count}/{required}*)"
                case "A Hat in Time":
                    if item == "Time Piece" and not settings['Death Wish Only']:
                        required = 0
                        match settings['End Goal']:
                            case 'Finale':
                                required = settings['Chapter 5 Cost']
                            case 'Rush Hour':
                                required = settings['Chapter 7 Cost']
                        return f"{item} (*{count}/{required}*)"
                    if item == "Progressive Painting Unlock":
                        required = 3
                        return f"{item} ({count}/{required})"
                    if item.startswith("Metro Ticket"):
                        required = 4
                        tickets = ["Yellow", "Green", "Blue", "Pink"]
                        collected = [player.get_collected_items(f"Metro Ticket - {ticket}" for ticket in tickets)]
                        return f"{item} ({''.join([key[0] for key in collected]) if len(collected) > 0 else "0"}/{required})"
                    if item.startswith("Relic"):
                        relics = {
                            "Burger": [
                                "Relic (Burger Cushion)",
                                "Relic (Burger Patty)"
                            ],
                            "Cake": [
                                "Relic (Cake Stand)",
                                "Relic (Chocolate Cake Slice)",
                                "Relic (Chocolate Cake)",
                                "Relic (Shortcake)"
                            ],
                            "Crayon": [
                                "Relic (Blue Crayon)",
                                "Relic (Crayon Box)",
                                "Relic (Green Crayon)",
                                "Relic (Red Crayon)"
                            ],
                            "Necklace": [
                                "Relic (Necklace Bust)",
                                "Relic (Necklace)"
                            ],
                            "Train": [
                                "Relic (Mountain Set)",
                                "Relic (Train)"
                            ],
                            "UFO": [
                                "Relic (Cool Cow)",
                                "Relic (Cow)",
                                "Relic (Tin-foil Hat Cow)",
                                "Relic (UFO)"
                            ]
                        }
                        for relic, parts in relics.items():
                            if any(part == item for part in parts):
                                required = len(parts)
                                count = len(player.get_collected_items(parts))
                                return f"{item} ({relic} {count}/{required})"
                case "A Short Hike":
                    if item == "Seashell":
                        return f"{item} ({count})"
                case "Archipela-Go!":
                    if settings['Goal'] == "Long Macguffin" and len(item) == 1:
                        items = list("Archipela-Go!")
                        collected = [player.get_collected_items(items)]
                        collected_string = ""
                        for i in items:
                            if i in collected: collected_string += i
                            else: collected_string += "_"
                        return f"{item} ({collected_string})"
                case "Celeste (Open World)":
                    if item == 'Strawberry':
                        total = settings['Total Strawberries']
                        required = round(total * (settings['Strawberries Required Percentage'] / 100))
                        return f"{item} *({count}/{required})*"
                case "Donkey Kong 64":
                    kongs = ["Donkey", "Diddy", "Lanky", "Tiny", "Chunky"]
                    shopkeepers = ["Candy", "Cranky", "Funky", "Snide"]
                    moves = { # Translate rando names to full names for convenience
                        "Barrels": "Barrel Throwing",
                        "Bongos": "Bongo Blast",
                        "Coconut": "Coconut Shooter",
                        "Feather": "Feather Bow",
                        "Grape": "Grape Shooter",
                        "Guitar": "Guitar Gazump",
                        "Oranges": "Orange Throwing",
                        "Peanut": "Peanut Popguns",
                        "Triangle": "Triangle Trample",
                        "Trombone": "Trombone Tremor",
                        "Vines": "Vine Swinging",
                    }
                    if item == "Banana Fairy":
                        total = 20
                        return f"{item} ({count}/{total})"
                    if item == "Golden Banana":
                        total = 201
                        return f"{item} ({count}/{total})"
                    if item.startswith("Key "):
                        keys = 8
                        collected_string = ""
                        for k in range(keys):
                                if f"Key {k+1}" in player.inventory: collected_string += str(k+1)
                                else: collected_string += "_"
                        return f"{item} ({collected_string})"
                    if item in kongs:
                        collected_string = ""
                        for kong in kongs:
                            if kong in player.inventory: collected_string += kong[0:1]
                            else: collected_string += "__"
                        return f"{item} Kong ({collected_string})"
                    if item in moves.keys():
                        return moves[item]
                case "Donkey Kong Country 3":
                    if item == "DK Coin":
                        required = settings['Dk Coins For Gyrocopter']
                        return f"{item} ({count}/{required})"
                case "DOOM 1993":
                    if item.endswith(" - Complete"):
                        count = len([i for i in player.inventory if i.endswith(" - Complete")])
                        required = 0
                        for episode in 1, 2, 3, 4:
                            if settings[f"Episode {episode}"] is True:
                                required = required + (1 if settings['Goal'] == "Complete Boss Levels" else 9)
                        return f"{item} ({count}/{required})"
                case "DOOM II":
                    if item.endswith(" - Complete"):
                        count = len([i for i in player.inventory if i.endswith(" - Complete")])
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
                case "Final Fantasy Mystic Quest":
                    if item == "Sky Fragment":
                        return f"{item} ({count})"
                case "gzDoom":
                    item_regex = re.compile(r"^([a-zA-Z]+) \((\S+)\)$")
                    if item.startswith("Level Access"):
                        count = len([i for i in player.inventory if i.startswith("Level Access")])
                        total = len(settings['Included Levels'])
                        return f"{item} ({count}/{total})"
                    if item.startswith("Level Clear"):
                        count = len([i for i in player.inventory if i.startswith("Level Clear")])
                        required = settings['Win Conditions']['nrof-maps']
                        if required == "all":
                            required = len(settings['Included Levels'])
                        return f"{item} ({count}/{required})"
                    if any([item.startswith(color) for color in ["Blue","Yellow","Red"]]) and not item == "BlueArmor":
                        item_match = item_regex.match(item)
                        subitem,map = item_match.groups()
                        collected_string = str()
                        keys = [f"{color}{key}" for color in ["Blue","Yellow","Red"] for key in ["Skull", "Card"]]
                        map_keys = sorted([i for i in item_table['gzDoom'].keys() if (i.endswith(f"({map})") and any([key in i for key in keys]))])
                        for i in map_keys:
                            if i in player.inventory: collected_string += i[0]
                            else: collected_string += "_"
                        if f"Level Access ({map})" not in player.inventory:
                            collected_string = f"~~{collected_string}~~" # Strikethrough keys if not found
                        return f"{item} ({collected_string})"
                case "Here Comes Niko!":
                    if item == "Cassette":
                        required = max({k: v for k, v in settings.items() if "Cassette Cost" in k}.values())
                        return f"{item} ({count}/{required})"
                    if item.endswith("Cassette") and settings['Cassette Logic'] == "Level Based":
                        total = 10
                        return f"{item} ({count}/{total})"
                    if item == "Coin":
                        required = 76 if settings['Completion Goal'] == "Employee" else settings['Elevator Cost']
                        return f"{item} (*{count}/{required}*)"
                    if item in ["Hairball City Fish", "Turbine Town Fish", "Salmon Creek Forest Fish", "Public Pool Fish", "Bathhouse Fish", "Tadpole HQ Fish"] and settings['Fishsanity'] == "Insanity":
                        required = 5
                        return f"{item} ({count}/{required})"
                case "Hollow Knight":
                    # There'll probably be something here later
                    return item.replace("_", " ").replace("-"," - ")
                case "Jigsaw":
                    if item.endswith("Puzzle Pieces"):
                        starting_pieces_item = None
                        for i in player.inventory:
                            if i.sender == "Archipelago":
                                if i.name.endswith("Puzzle Pieces"):
                                    starting_pieces_item = i
                                    break

                        starting_pieces: int = int(starting_pieces_item.name.split()[0]) if starting_pieces_item else 0
                        pieces_per_item: int = int(item.split()[0])
                        item_count: int = player.get_item_count(item)

                        total_pieces = starting_pieces + (pieces_per_item * item_count)
                        return f"{item} ({total_pieces} Available)"
                case "Muse Dash":
                    if item == "Music Sheet":
                        song_count = settings['Starting Song Count'] + settings['Additional Song Count']
                        total = round(song_count * (settings['Music Sheet Percentage'] / 100))
                        required = round(total / (settings['Music Sheets Needed to Win'] / 100))
                        return f"{item} ({count}/{required})"
                case "Ocarina of Time":
                    if item == "Triforce Piece" and settings['Triforce Hunt'] is True:
                        required = settings['Required Triforce Pieces']
                        return f"{item} ({count}/{required})"
                    if item == "Gold Skulltula Token":
                        required = 50
                        return f"{item} ({count}/{required})"
                    if item == "Progressive Wallet":
                        capacities = ["99", "200", "500", "999"]
                        return f"{item} ({capacities[player.get_item_count(item)]} Capacity)"
                    if item == "Piece of Heart":
                        if count % 4 == 0:
                            return f"{item} (+1 Heart Container)"
                case "Pokemon Mystery Dungeon Explorers of Sky":
                    sky_paths = [ "1st Station Pass", "2nd Station Pass", "3rd Station Pass", "4th Station Pass",
                        "5th Station Pass", "6th Station Pass", "7th Station Pass", "8th Station Pass",
                        "9th Station Pass", "Sky Peak Summit Pass" ]
                    instruments = ["Icy Flute", "Fiery Drum", "Terra Cymbal", "Aqua-Monica", "Rock Horn", "Grass Corner",
                                   "Sky Melodica", "Stellar Symphony", "Null Bagpipes", "Glimmer Harp", "Toxic Sax",
                                   "Biting Bass", "Knockout Bell", "Spectral Chimes", "Liar's Lyre", "Charge Synth",
                                   "Norma-ccordion", "Psychic Cello", "Dragu-teki", "Steel Guitar"]
                    seal_unlocks = [ "Ice Aegis Cave", "Rock Aegis Cave", "Steel Aegis Cave", "Aegis Cave Pit" ]

                    if item == "Progressive Sky Path":
                        return f"{item} ({sky_paths[count-1]})"
                case "Pizza Tower":
                    if item == "Toppin":
                        total = settings['Toppin Count']
                        required = max([settings[f'Floor {num} Boss Toppins'] for num in range(1, 6)])
                        return f"{item} ({count}/{required})"
                case "Simon Tatham's Portable Puzzle Collection":
                    # Tracking total access to puzzles instead of completion percentage, that's for the locations
                    total = settings['Puzzle Count']
                    count = len(player.inventory)
                    return f"{item} ({count}/{total})"
                case "Sonic Adventure 2 Battle":
                    if item == "Emblem":
                        required = round(settings['Max Emblem Cap'] * (settings["Emblem Percentage for Cannon's Core"] / 100))
                        return f"{item} ({count}/{required})"
                case "Super Cat Planet":
                    if item == "Cat":
                        total = 169
                        return f"{item} ({count}/{total})"
                case "Super Mario 64":
                    if item == "Power Star":
                        required = round(
                            settings['Total Power Stars']
                            * (settings['Endless Stairs Star %'] / 100)
                        )
                        return f"{item} ({count}/{required})"
                case "Super Mario World":
                    if item == "Yoshi Egg" and settings['Goal'] == "Yoshi Egg Hunt":
                        required = round(
                            settings['Max Number of Yoshi Eggs']
                            * (settings['Required Percentage of Yoshi Eggs'] / 100))
                        return f"{item} ({count}/{required})"
                    if item == "Boss Token":
                        required = settings['Bosses Required']
                        return f"{item} ({count}/{required})"
                case "Trackmania":
                    medals = ["Bronze Medal", "Silver Medal", "Gold Medal", "Author Medal"]
                    # From TMAP docs: 
                    # "The quicket medal equal to or below target difficulty is made the progression medal."
                    target_difficulty = settings['Target Time Difficulty']
                    progression_medal_lookup = target_difficulty // 100
                    progression_medal = medals[progression_medal_lookup]

                    if item == progression_medal:
                        total = len([l for l in player.locations.values() if l.location.endswith("Target Time")])
                        required = round(total * (settings['Series Medal Percentage'] / 100))
                        return f"{item} ({count}/{required})"
                case "TUNIC":
                    treasures = {
                        "DEF": ["Secret Legend", "Phonomath"],
                        "POTION": ["Spring Falls", "Just Some Pals", "Back To Work"],
                        "SP": ["Forever Friend", "Mr Mayor", "Power Up", "Regal Weasel"],
                        "MP": ["Sacred Geometry", "Vintage", "Dusty"]
                    }
                    if item == "Flask Shard":
                        flask_progress = player.get_item_count(item) % 3
                        return f"{item} ({"Gained Flask!" if flask_progress == 0 else f"{flask_progress}/3"})"
                    if item == "Fairy":
                        required = 20
                        if count < 10: required = 10
                        return f"{item} ({count}/{required})"
                    if item == "Gold Questagon":
                        required = settings['Gold Hexagons Required']
                        return f"{item} ({count}/{required})"
                    if item == "Golden Coin":
                        required = [3,6,10,15]
                        next_req = 0
                        for check in required:
                            if count >= check: continue
                            if count < check:
                                next_req = check
                                break
                        return f"{item} ({count}/{next_req})"
                    if item in ["Blue Questagon", "Red Questagon", "Green Questagon"]:
                        count = len(i for i in ["Blue Questagon", "Red Questagon", "Green Questagon"] if i in player.inventory)
                        required = 3
                        return f"{item} ({count}/{required})"
                    if item == "Sword Upgrade":
                        upgrades = ["Stick", "Ruin Seeker's Sword", "Librarian's Sword", "Heir's Sword"]
                        upgrade = upgrades[count-1]
                        return f"{item} (LV{count}: {upgrade})"
                    # Treasures
                    for stat, stat_items in treasures.items():
                        if item in stat_items:
                            return f"{item} (+1 {stat})"
                case "Twilight Princess":
                    if item == "Poe Soul":
                        required = 60
                        if count < 20: required = 20
                        return f"{item} ({count}/{required})"
                case "Void Stranger":
                    if item == "Greed Coin":
                        required = 15
                        return f"{item} ({count}/{required})"
                    if item == "Locust Idol" or item == "Lucky Locust Idol":
                        OneCount = player.get_item_count("Locust Idol")
                        ThreeCount = Player.get_item_count("Lucky Locust Idol")
                        count = OneCount + (ThreeCount * 3)
                        return f"{item} ({count})"
                case "Wario Land 4":
                    if item.endswith("Piece"):
                        # Gather up all the jewels
                        jewels = ["Emerald", "Entry Jewel", "Golden Jewel", "Ruby", "Sapphire", "Topaz"]
                        parts = ["Bottom Left", "Bottom Right", "Top Left", "Top Right"]
                        jewel = next(j for j in jewels if j in item)
                        #
                        jewel_count = len([i for i in player.inventory if f"{jewel} Piece" in i])
                        jewel_required = 4
                        jewels_complete = len(
                            [j for j in jewels
                            if len([f"{part} {j} Piece" for part in parts
                                if f"{part} {j} Piece" in player.inventory]) == 4 ])
                        jewels_required = settings['Required Jewels']
                        return f"{item} ({jewel_count}/{jewel_required}P|{jewels_complete}/{jewels_required}C)"
                case _:
                    return item
        except Exception as e:
            logger.error(f"Error while parsing tracking info for item {item} in game {game}:", e, exc_info=True)
            # If we can't parse the item, just return the name
            # This is to prevent the bot from crashing if something goes wrong
            # with the settings or the item name.
            return item
    # If the item is not in the settings, return the name as is

    # Return the same name if nothing matched (or no settings available)
    return item

def handle_location_tracking(game: Game, player: Player, item: Item):
    """If checking a location is an indicator of progress, we should track that in the location name."""

    ItemObject = item
    location = item.location

    if bool(player.settings):
        settings = player.settings
        game = player.game

        match game:
            case "Hollow Knight":
                # There'll probably be something here later
                return location.replace("_", " ").replace("-"," - ")
            case "Simon Tatham's Portable Puzzle Collection":
                required = round(settings['Puzzle Count']
                                 * (settings['Target Completion Percentage'] / 100))
                count = player.collected_locations
                return f"{location} ({count}/{required})"
            # case "Trackmania":
            #     if location.endswith("Target Time"):
            #         total = len([l for l in player.locations.values() if l.location.endswith("Target Time")])
            #         required = round(total * (settings['Series Medal Percentage'] / 100))
            #         count = len([l for l in player.locations.values() if l.location.endswith("Target Time") and l.found is True])
            #         return f"{location} ({count}/{required})"
            case _:
                return location
    return location

def handle_location_hinting(player: Player, item: Item) -> tuple[list[str], str]:
    """Some locations have a cost or extra info associated with it.
    If an item that's hinted is on this location, go through similar steps to
    the tracking functions to provide info on costs etc."""

    location = item.location

    requirements = []
    extra_info = ""

    if bool(player.settings):
        settings = player.settings
        game = item.game

        match game:
            case "Here Comes Niko!":
                contact_lists = {
                    "1": [
                        f"Hairball City - {npc}" for npc in ["Mitch", "Mai", "Moomy", "Blippy Dog", "Nina"]
                    ] + [
                        f"Turbine Town - {npc}" for npc in ["Mitch", "Mai", "Blippy Dog"]
                    ] + [
                        f"Salmon Creek Forest - {npc}" for npc in (["SPORTVIVAL", "Mai"]
                        + ["Fish with Fischer", "Bass", "Catfish", "Pike", "Salmon", "Trout"])
                    ],
                    "2": [
                        f"Hairball City - {npc}" for npc in ["Game Kid", "Blippy", "Serschel & Louist"]
                    ] + [
                        f"Turbine Town - {npc}" for npc in ["Blippy", "Serschel & Louist"]
                    ] + [
                        f"Salmon Creek Forest - {npc}" for npc in ["Game Kid", "Blippy", "Serschel & Louist"]
                    ] + [
                        f"Public Pool - {npc}" for npc in (["Mitch", "SPORTVIVAL VOLLEY", "Blessley"]
                        + ["Little Gabi's Flowers"] + [f"Flowerbed {num+1}" for num in range(3)])
                    ] + [
                        f"Bathhouse - {npc}" for npc in (["Blessley", "Blippy", "Blippy Dog"]
                        + ["Little Gabi's Flowers"] + [f"Flowerbed {num+1}" for num in range(3)]
                        + ["Fish with Fischer", "Anglerfish", "Clione", "Jellyfish", "Little Wiggly Guy", "Pufferfish"])
                    ]
                }

                level = None
                npc = None
                try:
                    level, npc = location.split(" - ")
                except ValueError:
                    level = location
                    npc = None

                if f"{location} Cassette Cost" in settings:
                    # Get the cassette cost
                    cost = settings[f"{location} Cassette Cost"]

                    # Cassette Requirements
                    if settings['Cassette Logic'] == "Level Based":
                        requirements.append(f"{cost} {level} Cassettes")
                    else:
                        requirements.append(f"{cost} Cassettes")

                if f"Kiosk {level} Cost" in settings and location == f"{level} - Kiosk":
                    # Get the kiosk cost
                    cost = settings[f"Kiosk {level} Cost"]

                    requirements.append(f"{cost} Coins")

                # Contact List Requirements
                if location in contact_lists["1"]:
                    requirements.append("Contact List 1")
                if location in contact_lists["2"]:
                    requirements.append("Contact List 2")

    if bool(requirements):
        logger.info(f"Updating item's location {item.location} with requirements: {requirements}")
    return (requirements, extra_info)

def build_game_state(game: dict, player_str: str) -> discord.Embed:
    """For Discord: Use the tracked game state to build a human-readable view
    of a player slot's progress toward their goal."""

    player: Player = game['players'][player_str]
    player_game = player['game']

    goal: str
    goal_str: str

    readable_state = discord.Embed(
        title = f"{player_str} ({player_game})",
        description = None
    )

    if player['goaled']:
        readable_state.description = "This slot is victorious."
    if player['released']:
        readable_state.description = "This slot has released."

    match player_game:
        case "A Hat in Time":
            hats = ["Sprint Hat", "Brewing Hat", "Ice Hat", "Dweller Mask", "Time Stop Hat"]
            collected_hats = player.get_collected_items(hats)
            time_pieces = player.get_item_count("Time Piece")
            time_pieces_required: int

            goal = player['settings']['End Goal']
            match goal:
                case "Finale":
                    goal_str = "Defeat Mustache Girl"
                    time_pieces_required = player.settings['Chapter 5 Cost']
                case "Rush Hour":
                    goal_str = "Escape Nyakuza Metro's Rush Hour"
                    time_pieces_required = player.settings['Chapter 7 Cost']
                case "Seal The Deal":
                    goal_str = "Seal the Deal with Snatcher"
                case _:
                    goal_str = goal
            if goal == "Finale" or goal == "Rush Hour":
                readable_state.add_field(name="Time Pieces", value=f"{time_pieces} found\n{time_pieces_required} required", inline = True)
            readable_state.add_field(name="Found Hats", value="\n".join([hat.name for hat in collected_hats]), inline = True)

            if goal == "Rush Hour":
                metro_tickets = ["Yellow", "Pink", "Green", "Blue"]
                collected_tickets = player.get_collected_items([f"Metro Ticket - {color}" for color in metro_tickets])
                readable_state.add_field(name="Found Tickets", value="\n".join([ticket.name for ticket in collected_tickets]), inline = True)

            world_costs = {
                "Kitchen": player['settings']['Chapter 1 Cost'],
                "Machine Room": player['settings']['Chapter 2 Cost'],
                "Bedroom": player['settings']['Chapter 3 Cost'],
                "Boiler Room": player['settings']['Chapter 4 Cost'],
                "Attic": player['settings']['Chapter 5 Cost'],
                "Laundry": player['settings']['Chapter 6 Cost'],
                "Lab": player['settings']['Chapter 7 Cost'],
            }
            accessible_worlds = [k for k, v in world_costs.items() if time_pieces > v]
            readable_state.add_field(name="Accessible Telescopes", value = "\n".join(accessible_worlds))
        
        case "Here Comes Niko!":
            coins = player.get_item_count("Coin")
            coins_required: int

            goal = player['settings']['Completion Goal']
            match goal:
                case "Hired":
                    goal_str = "Get Hired as a Professional Friend"
                    coins_required = player['settings']['Elevator Cost']
                case _:
                    goal_str = goal

            # TODO Match Abilities

        case "Ocarina of Time":
            max_hearts = 20
            starting_hearts = 3
            heart_containers = player.get_item_count("Heart Container")
            heart_pieces = player.get_item_count("Piece of Heart")
            completed_heart_pieces = heart_pieces // 4
            partial_hearts = heart_pieces % 4

            current_hearts = starting_hearts + heart_containers + completed_heart_pieces

            # TODO Get main inventory
        
        case "TUNIC":
            treasures = {
                "DEF": ["Secret Legend", "Phonomath"],
                "POTION": ["Spring Falls", "Just Some Pals", "Back To Work"],
                "SP": ["Forever Friend", "Mr Mayor", "Power Up", "Regal Weasel"],
                "MP": ["Sacred Geometry", "Vintage", "Dusty"]
            }

            logical_att = player.get_item_count("ATT Offering")
            logical_def = player.get_item_count("DEF Offering") + len(player.get_collected_items(treasures['DEF']))
            logical_hp = player.get_item_count("HP Offering")
            logical_sp = player.get_item_count("SP Offering") + len(player.get_collected_items(treasures['SP']))
            logical_mp = player.get_item_count("MP Offering") + len(player.get_collected_items(treasures['MP']))
            logical_potion = player.get_item_count("POTION Offering") + len(player.get_collected_items(treasures['POTION']))

        case _:
            pass

    readable_state.description = f"Your goal is to **{goal_str}**."

    return readable_state
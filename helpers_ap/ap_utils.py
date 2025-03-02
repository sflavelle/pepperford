import datetime
import time
import psycopg2 as psql
import logging
from zoneinfo import ZoneInfo

# setup logging
logger = logging.getLogger('ap_itemlog')

classification_cache = {}

class Item:
    """An Archipelago item in the multiworld"""
    def __init__(self, sender: str, receiver: str, item: str, location: str, game: str = None, entrance: str = None):
        self.sender = sender
        self.receiver = receiver
        self.name = item
        self.game = game
        self.location = location
        self.location_entrance = entrance
        self.classification = item_classification(self)
        self.found = False
        self.hinted = False
        self.spoiled = False

        if self.game == None:
            logger.warning(f"Item object for {self.name} has no game associated with it?")
    
    def __str__(self):
        return self.name

    def collect(self):
        self.found = True

    def hint(self):
        self.hinted = True

    def spoil(self):
        self.spoiled = True

    def is_found(self):
        return self.found
    
    def is_filler(self):
        return self.classification == "filler"
    def is_currency(self):
        return self.classification == "currency"

class CollectedItem(Item):
    def __init__(self, sender, receiver, item, location, game: str = None):
        super().__init__(sender, receiver, item, location, game)
        self.locations = [f"{sender} - {location}"]
        self.count: int = 0

    def collect(self, sender, location):
        self.found = True
        self.locations.append(f"{sender} - {location}")
        self.count = len(self.locations)

class Player:
    def __init__(self,name,game):
        self.name = name
        self.game = game
        self.items = {}
        self.locations = {}
        self.settings = PlayerSettings()
        self.goaled = False
        self.released = False
    
    def collect(self, item: Item|CollectedItem):
        if item.name in self.items:
            self.items[item.name].collect(item.sender, item.location)
        else:
            self.items.update({item.name: item})
            self.items[item.name].collect(item.sender, item.location)

    def send(self, item: Item|CollectedItem):
        if item.location not in self.locations:
            self.locations.update({item.location: item})
        self.locations[item.location].found = True

    def is_finished(self) -> bool:
        return self.goaled or self.released
    
    def is_goaled(self) -> bool:
        return self.goaled
    

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

def handle_item_tracking(player: Player, item: str):
    """If an item is an important collectable of some kind, we should put some extra info in the item name for the logs."""

    if bool(player.settings):
        settings = player.settings
        game = player.game

        match game:
            case "A Link to the Past":
                if item == "Triforce Piece" and "Triforce Hunt" in settings['Goal']:
                    required = settings['Triforce Pieces Required']
                    count = player.items[item].count
                    return f"{item} ({count}/{required})"
            case "A Hat in Time":
                if item == "Time Piece" and not settings['Death Wish Only']:
                    required = 0
                    match settings['End Goal']:
                        case 'Finale':
                            required = settings['Chapter 5 Cost']
                        case 'Rush Hour':
                            required = settings['Chapter 7 Cost']
                    count = player.items[item].count
                    return f"{item} ({count}/{required})"
                if item == "Progressive Painting Unlock":
                    required = 3
                    count = player.items[item].count
                    return f"{item} ({count}/{required})"
                if item.startswith("Metro Ticket"):
                    required = 4
                    tickets = ["Yellow", "Green", "Blue", "Pink"]
                    collected = [ticket for ticket in tickets if f"Metro Ticket - {ticket}" in player.items]
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
                            count = len([i for i in player.items if i in parts])
                            return f"{item} ({relic} {count}/{required})"

            case "DOOM 1993":
                if item.endswith(" - Complete"):
                    count = len([i for i in player.items if i.endswith(" - Complete")])
                    required = 0
                    for episode in 1, 2, 3, 4:
                        if settings[f"Episode {episode}"] is True:
                            required = required + (1 if settings['Goal'] == "Complete Boss Levels" else 9)
                    return f"{item} ({count}/{required})"
            case "DOOM II":
                if item.endswith(" - Complete"):
                    count = len([i for i in player.items if i.endswith(" - Complete")])
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
                    required = max({k: v for k, v in settings.items() if "Cassette Cost" in k}.values())
                    count = player.items[item].count
                    return f"{item} ({count}/{required})"
                if item == "Coin":
                    required = 76 if settings['Completion Goal'] == "Employee" else settings['Elevator Cost']
                    count = player.items[item].count
                    return f"{item} ({count}/{required})"
                if item in ["Hairball City Fish", "Turbine Town Fish", "Salmon Creek Forest Fish", "Public Pool Fish", "Bathhouse Fish", "Tadpole HQ Fish"] and settings['Fishsanity'] == "Insanity":
                    required = 5
                    count = player.items[item].count
                    return f"{item} ({count}/{required})"
            case "Ocarina of Time":
                if item == "Triforce Piece" and settings['Triforce Hunt'] is True:
                    required = settings['Required Triforce Pieces']
                    count = player.items[item].count
                    return f"{item} ({count}/{required})"
                if item == "Gold Skulltula Token":
                    required = 50
                    count = player.items[item].count
                    return f"{item} ({count}/{required})"
            case "Simon Tatham's Portable Puzzle Collection":
                # Tracking total access to puzzles instead of completion percentage, that's for the locations
                total = settings['puzzle_count']
                count = len(player.items)
                return f"{item} ({count}/{total})"
            case "Sonic Adventure 2 Battle":
                if item == "Emblem":
                    required = round(settings['Max Emblem Cap'] * (settings["Emblem Percentage for Cannon's Core"] / 100))
                    count = player.items[item].count
                    return f"{item} ({count}/{required})"
            case "Super Mario World":
                if item == "Yoshi Egg" and settings['Goal'] == "Yoshi Egg Hunt":
                    count = player.items[item].count
                    required = round(
                        settings['Max Number of Yoshi Eggs']
                        * (settings['Required Percentage of Yoshi Eggs'] / 100))
                    return f"{item} ({count}/{required})"
            case "TUNIC":
                if item == "Gold Questagon":
                    count = player.items[item].count
                    required = settings['Gold Hexagons Required']
                    return f"{item} ({count}/{required})"
                if item in ["Blue Questagon", "Red Questagon", "Green Questagon"]:
                    count = len(i for i in ["Blue Questagon", "Red Questagon", "Green Questagon"] if i in player.items)
                    required = 3
                    return f"{item} ({count}/{required})"
            case "Wario Land 4":
                if item.endswith("Piece"):
                    # Gather up all the jewels
                    jewels = ["Emerald", "Entry Jewel", "Golden Jewel", "Ruby", "Sapphire", "Topaz"]
                    parts = ["Bottom Left", "Bottom Right", "Top Left", "Top Right"]
                    jewel = next(j for j in jewels if j in item)
                    # 
                    jewel_count = len([i for i in player.items if f"{jewel} Piece" in i])
                    jewel_required = 4
                    jewels_complete = len(
                        [j for j in jewels 
                         if len([f"{part} {j} Jewel Piece" for part in parts
                             if f"{part} {j} Jewel Piece" in player.items]) == 4 ])
                    jewels_required = settings['Required Jewels']
                    return f"{item} ({jewel_count}/{jewel_required}P|{jewels_complete}/{jewels_required}C)"
            case _:
                return item
    
    # Return the same name if nothing matched (or no settings available)
    return item

def handle_location_tracking(player: Player, location: str):
    """If checking a location is an indicator of progress, we should track that in the location name."""

    if bool(player.settings):
        settings = player.settings
        game = player.game

        match game:
            case "Simon Tatham's Portable Puzzle Collection":
                required = round(settings['puzzle_count'] 
                                 * (settings['Target Completion Percentage'] / 100))
                count = len([loc for loc in player.locations.values() if loc.is_found()])
                return f"{location} ({count}/{required})"
            case _:
                return location
    return location

sqlcon = psql.connect(
    dbname="archipelago",
    user="postgres",
    host="localhost"
)


def item_classification(item: Item|CollectedItem, player: Player = None):
    """Refer to the itemdb and see whether the provided Item has a classification.
    If it doesn't, creates a new entry for that item with no classification.
    
    We can pass this through an intermediate step to assume things about
    some common items, but not everything.
    """

    global classification_cache
    permitted_values = ["progression", "conditional progression", "useful", "currency", "filler", "trap"]
    response = None # What we will ultimately return

    cache_timeout = 2*60*60 # 2 hours

    if item.game is None:
        return None

    if (item.game in classification_cache and item.name in classification_cache[item.game]):
        if time.time() - classification_cache[item.game][item.name][1] < cache_timeout:
            logger.info(f"Invalidating cache for {item.game}: {item.name}")
        else:
            return classification_cache[item.game][item.name][0]

    def progression_condition(prog_setting: str, value_true: str, value_false: str):
        """If an item is classified differently based on a world setting, see if that setting is true.
        If so, return value_true. Otherwise, return value_false."""

        if prog_setting in player.settings:
            if player.settings[prog_setting] is True: return value_true
        else: return value_false

    # Some games are 'simple' enough that everything (or near everything) is progression
    match item.game:
        case "Simon Tatham's Portable Puzzle Collection":
            if item.name == "Filler": response = "filler"
            else: response = "progression"
        case "SlotLock"|"APBingo": response = "progression" # metagames are generally always progression
        case _:
            cursor = sqlcon.cursor()

            cursor.execute("CREATE TABLE IF NOT EXISTS item_classification (game bpchar, item bpchar, classification varchar(32))")

            try:
                cursor.execute("SELECT classification FROM item_classification WHERE game = %s AND item = %s;", (item.game, item.name))
                response = cursor.fetchone()[0]
                logger.debug(response)
                if response == "conditional progression":
                    # Progression in certain settings, otherwise useful/filler
                    pass #for now
                elif response not in permitted_values:
                    response = None
            except TypeError:
                logger.debug("Nothing found for this item, likely")
                logger.info(f"itemsdb: adding {item.game}: {item.name} to the db")
                cursor.execute("INSERT INTO item_classification VALUES (%s, %s, %s)", (item.game, item.name, None))
            finally:
                sqlcon.commit()
    logger.debug(f"itemsdb: classified {item.game}: {item.name} as {response}")
    if item.game not in classification_cache:
        classification_cache[item.game] = {}
    classification_cache[item.game][item.name] = (response.lower(), time.time()) if bool(response) else (None, time.time())
    return classification_cache[item.game][item.name][0]

import datetime
import time
from zoneinfo import ZoneInfo

class Item:
    """An Archipelago item in the multiworld"""
    def __init__(self, sender, receiver, item, location, entrance = None):
        self.sender = sender
        self.receiver = receiver
        self.name = item
        self.location = location
        self.location_entrance = entrance
        self.found = False
        self.hinted = False
        self.spoiled = False
    
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

class CollectedItem(Item):
    def __init__(self, sender, receiver, item, location):
        super().__init__(sender, receiver, item, location)
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
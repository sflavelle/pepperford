import datetime
import time
from zoneinfo import ZoneInfo

class Item:
    """An Archipelago item in the multiworld"""
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
        self.locations = [f"{sender} - {location}"]
        self.count: int = 0

    def collect(self, sender, location):
        self.found = True
        self.count = self.count + 1
        self.locations.append(f"{sender} - {location}")

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
        if item in self.items:
            self.items[item].collect(item.sender, item.location)
        else:
            self.items.update({item.name: item})

    def is_finished(self) -> bool:
        return self.goaled or self.released

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

        self.timestamp = time.mktime(datetime.datetime(tzinfo=ZoneInfo(time.tzname)).strptime(timestamp, "%Y-%m-%d %H:%M:%S,%f"))
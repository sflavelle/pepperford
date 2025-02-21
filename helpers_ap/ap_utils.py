from .ap_classes import Player, Item, CollectedItem

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
                if item.endswith("Jewel Piece"):
                    # Gather up all the jewels
                    jewels = ["Emerald", "Entry", "Golden", "Ruby", "Sapphire", "Topaz"]
                    parts = ["Bottom Left", "Bottom Right", "Top Left", "Top Right"]
                    jewel = next(j for j in jewels if j in item)
                    # 
                    jewel_count = len([i for i in player.items if f"{jewel} Jewel Piece" in i])
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
                count = len([loc for loc in player.locations if loc.found is True])
                return f"{location} ({count}/{required})"
            case _:
                return location
    return location
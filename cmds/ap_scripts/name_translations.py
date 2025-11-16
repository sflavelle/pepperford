class gzDoomMapNames(dict):

    def lookupMap(self, wadname: str, mapname: str):
        """Return a map's 'friendly' name for a supported WAD."""

        match wadname:
            case "Doom":
                return self.DOOM1[mapname]
            case "Doom 2":
                return self.DOOM2[mapname]
            case "No Rest for the Living":
                return self.NROTL[mapname]
            case "TNT":
                return self.TNT[mapname]
            case "Plutonia":
                return self.PLUTONIA[mapname]
            case "WadFusion":
                try:
                    # different lookups based on prefix
                    if mapname.startswith(("E1M", "E2M", "E3M", "E4M")):
                        return self.DOOM1[mapname]
                    elif mapname.startswith("E5M"):
                        return self.SIGIL1[mapname]
                    elif mapname.startswith("E6M"):
                        return self.SIGIL2[mapname]
                    elif mapname.startswith("MAP"):
                        return self.DOOM2[mapname]
                    elif mapname.startswith("NV"):
                        return self.NROTL[mapname[2:]] # strip 'NV_' prefix
                    elif mapname.startswith("LR"):
                        return self.RUST[mapname[2:]] # strip 'LR_' prefix
                    elif mapname.startswith("TN"):
                        return self.TNT[mapname[2:]] # strip 'TN_' prefix
                    elif mapname.startswith("PL"):
                        return self.PLUTONIA[mapname[2:]] # strip 'PL_' prefix
                    else:
                        return None
                finally:
                    return None
            case _:
                return None


    DOOM1 = {
        "E1M1": "Hangar",
        "E1M2": "Nuclear Plant",
        "E1M3": "Toxin Refinery",
        "E1M4": "Command Control",
        "E1M5": "Phobos Lab",
        "E1M6": "Central Processing",
        "E1M7": "Computer Station",
        "E1M8": "Phobos Anomaly",
        "E1M9": "Military Base",
        "E2M1": "Deimos Anomaly",
        "E2M2": "Containment Area",
        "E2M3": "Refinery",
        "E2M4": "Deimos Lab",
        "E2M5": "Command Center",
        "E2M6": "Halls of the Damned",
        "E2M7": "Spawning Vats",
        "E2M8": "Tower of Babel",
        "E2M9": "Fortress of Mystery",
        "E3M1": "Hell Keep",
        "E3M2": "Slough of Despair",
        "E3M3": "Pandemonium",
        "E3M4": "House of Pain",
        "E3M5": "Unholy Cathedral",
        "E3M6": "Mt. Erebus",
        "E3M7": "Limbo",
        "E3M8": "Dis",
        "E3M9": "Warrens",
        "E4M1": "Hell Beneath",
        "E4M2": "Perfect Hatred",
        "E4M3": "Sever the Wicked",
        "E4M4": "Unruly Evil",
        "E4M5": "They Will Repent",
        "E4M6": "Against Thee Wickedly",
        "E4M7": "And Hell Followed",
        "E4M8": "Unto the Cruel",
        "E4M9": "Fear",

        # Xbox Edition shenanigans
        "E1M10": "Sewers",

    }

    SIGIL1 = {
        "E5M1": "Baphomet's Demesne",
        "E5M2": "Sheol",
        "E5M3": "Cages of the Damned",
        "E5M4": "Paths of Wretchedness",
        "E5M5": "Abaddon's Void",
        "E5M6": "Unspeakable Persecution",
        "E5M7": "Nightmare Underworld",
        "E5M8": "Halls of Perdition",
        "E5M9": "Realm of Iblis",
    }

    SIGIL2 = {
        "E6M1": "Cursed Darkness",
        "E6M2": "Violent Hatred",
        "E6M3": "Twilight Desolation",
        "E6M4": "Fragments of Sanity",
        "E6M5": "Wrathful Reckoning",
        "E6M6": "Vengeance Unleashed",
        "E6M7": "Descent Into Terror",
        "E6M8": "Abyss of Despair",
        "E6M9": "Shattered Homecoming",
    }

    DOOM2 = {
        "MAP01": "Entryway",
        "MAP02": "Underhalls",
        "MAP03": "The Gantlet",
        "MAP04": "The Focus",
        "MAP05": "The Waste Tunnels",
        "MAP06": "The Crusher",
        "MAP07": "Dead Simple",
        "MAP08": "Tricks and Traps",
        "MAP09": "The Pit",
        "MAP10": "Refueling Base",
        "MAP11": "'O' of Destruction!",
        "MAP12": "The Factory",
        "MAP13": "Downtown",
        "MAP14": "The Inmost Dens",
        "MAP15": "Industrial Zone",
        "MAP16": "Suburbs",
        "MAP17": "Tenements",
        "MAP18": "The Courtyard",
        "MAP19": "The Citadel",
        "MAP20": "Gotcha!",
        "MAP21": "Nirvana",
        "MAP22": "The Catacombs",
        "MAP23": "Barrels o' Fun",
        "MAP24": "The Chasm",
        "MAP25": "Bloodfalls",
        "MAP26": "The Abandoned Mines",
        "MAP27": "Monster Condo",
        "MAP28": "The Spirit World",
        "MAP29": "The Living End",
        "MAP30": "Icon of Sin",
        "MAP31": "Wolfenstein",
        "MAP32": "Grosse",

        # more xbox version shenanigans
        "MAP33": "Betray",
    }

    NROTL = { # "No Rest for the Living", NERVE.WAD, 'NV' maps
        "MAP01": "The Earth Base",
        "MAP02": "The Pain Labs",
        "MAP03": "Canyon of the Dead",
        "MAP04": "Hell Mountain",
        "MAP05": "Vivisection",
        "MAP06": "Inferno of Blood",
        "MAP07": "Baron's Banquet",
        "MAP08": "Tomb of Malevolence",
        "MAP09": "March of the Demons",
    }

    RUST = { # Legacy of Rust, 'LR' maps
        "MAP01": "Scar Gate",
        "MAP02": "Sanguine Wastes",
        "MAP03": "Spirit Drains",
        "MAP04": "Descending Inferno",
        "MAP05": "Creeping Hate",
        "MAP06": "The Coiled City",
        "MAP07": "Forfeited Salvation",
        "MAP15": "Ash Mill",

        "MAP08": "Second Coming",
        "MAP09": "Falsehood",
        "MAP10": "Dis Union",
        "MAP11": "Echoes of Pain",
        "MAP12": "The Rack",
        "MAP13": "Soul Silo",
        "MAP14": "Brink",
        "MAP16": "Panopticon",
    }

    TNT = { # TNT: Evilution, 'TN' maps
        "MAP01": "System Control",
        "MAP02": "Human BBQ",
        "MAP03": "Power Control",
        "MAP04": "Wormhole",
        "MAP05": "Hanger",
        "MAP06": "Open Season",

        "MAP07": "Prison",
        "MAP08": "Metal",
        "MAP09": "Stronghold",
        "MAP10": "Redemption",
        "MAP11": "Storage Facility",

        "MAP12": "Crater",
        "MAP13": "Nukage Processing",
        "MAP14": "Steel Works",
        "MAP15": "Dead Zone",
        "MAP16": "Deepest Reaches",
        "MAP17": "Processing Area",
        "MAP18": "Mill",
        "MAP19": "Shipping/Respawning",
        "MAP20": "Central Processing",

        "MAP21": "Administration Center",
        "MAP22": "Habitat",
        "MAP23": "Lunar Mining Project",
        "MAP24": "Quarry",
        "MAP25": "Baron's Den",
        "MAP26": "Ballistyx",
        "MAP27": "Mount Pain",
        "MAP28": "Heck",
        "MAP29": "River Styx",
        "MAP30": "Last Call",

        "MAP31": "Pharaoh",
        "MAP32": "Caribbean",
    }

    PLUTONIA = { # The Plutonia Experiment, 'PL' maps
        "MAP01": "Congo",
        "MAP02": "Well of Souls",
        "MAP03": "Aztec",
        "MAP04": "Caged",
        "MAP05": "Ghost Town",
        "MAP06": "Baron's Lair",
        "MAP07": "Caughtyard",
        "MAP08": "Realm",
        "MAP09": "Abattoire",
        "MAP10": "Onslaught",
        "MAP11": "Hunted",

        "MAP12": "Speed",
        "MAP13": "The Crypt",
        "MAP14": "Genesis",
        "MAP15": "The Twilight",
        "MAP16": "The Omen",
        "MAP17": "Compound",
        "MAP18": "Neurosphere",
        "MAP19": "NME",
        "MAP20": "The Death Domain",

        "MAP21": "Slayer",
        "MAP22": "Impossible Mission",
        "MAP23": "Tombstone",
        "MAP24": "The Final Frontier",
        "MAP25": "The Temple of Darkness",
        "MAP26": "Bunker",
        "MAP27": "Anti-Christ",
        "MAP28": "The Sewers",
        "MAP29": "Odyssey of Noises",
        "MAP30": "The Gateway of Hell",

        "MAP31": "Cyberden",
        "MAP32": "Go 2 It",
    }
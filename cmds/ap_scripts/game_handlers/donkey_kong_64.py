"""
Game handler for Donkey Kong 64.

Handles item tracking, location tracking, and state/goal tracking
for the DK64 Archipelago randomizer.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Tuple

logger = logging.getLogger("ap_itemlog.game_handlers.donkey_kong_64")


# ---------------------------------------------------------------------------
# DK64-specific data
# ---------------------------------------------------------------------------

KONGS = ["Donkey", "Diddy", "Lanky", "Tiny", "Chunky"]

# Translate rando names to full names for convenience
MOVES: dict[str, str] = {
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


class DK64Handler:
    """Handler for Donkey Kong 64 game-specific tracking."""

    game = "Donkey Kong 64"

    # ------------------------------------------------------------------
    # Item tracking
    # ------------------------------------------------------------------

    def handle_item_tracking(
        self, game: Any, player: Any, item: Any
    ) -> Optional[str]:
        item_name = item.name if hasattr(item, "name") else item
        slot_data = player.slot_data
        settings = player.settings

        if item_name == "Banana Fairy":
            return self._track_banana_fairy(player, item_name, slot_data, settings)
        if item_name == "Banana Medal":
            return self._track_banana_medal(player, item_name, settings)
        if item_name.endswith(" Blueprint"):
            return self._track_blueprint(player, item_name, slot_data, settings)
        if item_name == "Pearl":
            return self._track_pearl(player, item_name, slot_data, settings)
        if item_name == "Rainbow Coin":
            return self._track_rainbow_coin(player, item_name, slot_data, settings)
        if item_name in ("Rareware Coin", "Nintendo Coin"):
            return self._track_company_coins(player, item_name)
        if item_name == "Golden Banana":
            return self._track_golden_banana(player, item_name, slot_data, settings)
        if item_name.startswith("Key "):
            return self._track_keys(player, item_name)
        if item_name in KONGS:
            return self._track_kongs(player, item_name)
        if item_name in MOVES:
            return MOVES[item_name]

        return None  # fall through to default

    def _track_banana_fairy(
        self, player: Any, item: str, slot_data: dict, settings: dict
    ) -> str:
        count = player.get_item_count(item)
        blocker_fairies = [
            int(x[0]) for x in slot_data["BLockerValues"] if x[1] == "Fairy"
        ]
        blocker_fairies.append(settings["Rareware GB Requirment"])  # sic
        required = max(blocker_fairies)
        total = 20
        return f"{item} (*{count}/{required}*/{total})"

    def _track_banana_medal(
        self, player: Any, item: str, settings: dict
    ) -> str:
        count = player.get_item_count(item)
        required = settings["Jetpac Requirement"]
        return f"{item} (*{count}/{required}*)"

    def _track_blueprint(
        self, player: Any, item: str, slot_data: dict, settings: dict
    ) -> str:
        kong = item.replace(" Blueprint", "")
        count = player.get_item_count(item)
        individual_total = 8
        blocker_blueprints = [
            int(x[0]) for x in slot_data["BLockerValues"] if x[1] == "Blueprint"
        ]
        if len(blocker_blueprints) > 0:
            result = f"Blueprint ({kong} {count}/{individual_total})"
            if bool(settings.get("Chaos B. Lockers", False)):
                all_blueprint_count = len(
                    player.get_collected_items([f"{k} Blueprint" for k in KONGS])
                )
                result += f" ({all_blueprint_count}/{max(blocker_blueprints)})"
            return result
        return None

    def _track_pearl(
        self, player: Any, item: str, slot_data: dict, settings: dict
    ) -> str:
        count = player.get_item_count(item)
        blocker_pearls = [
            int(x[0]) for x in slot_data["BLockerValues"] if x[1] == "Pearl"
        ]
        blocker_pearls.append(settings["Mermaid Requirement"])
        required = max(blocker_pearls)
        total = 5
        return f"{item} (*{count}/{required}*)"

    def _track_rainbow_coin(
        self, player: Any, item: str, slot_data: dict, settings: dict
    ) -> Optional[str]:
        count = player.get_item_count(item)
        blocker_rainbowcoins = [
            int(x[0]) for x in slot_data["BLockerValues"] if x[1] == "RainbowCoin"
        ]
        if bool(settings.get("Chaos B. Lockers", False)) and bool(blocker_rainbowcoins):
            return f"{item} (*{count}/{max(blocker_rainbowcoins)}*)"
        return None

    def _track_company_coins(self, player: Any, item: str) -> str:
        count = len(
            player.get_collected_items(["Rareware Coin", "Nintendo Coin"])
        )
        total = 2
        return f"{item} (*{count}/{total}*)"

    def _track_golden_banana(
        self, player: Any, item: str, slot_data: dict, settings: dict
    ) -> str:
        count = player.get_item_count(item)
        blocker_gbs = max(
            int(BLock[0])
            for BLock in slot_data["BLockerValues"].values()
            if BLock[1] == "GoldenBanana"
        )
        total = 201
        if count > blocker_gbs:
            return f"{item} (*{count}*/{total})"
        else:
            return f"{item} (*{count}/{blocker_gbs}*/{total})"

    def _track_keys(self, player: Any, item: str) -> str:
        count = player.get_item_count(item)
        keys = 8
        collected_string = ""
        for k in range(keys):
            if player.has_item(f"Key {k + 1}"):
                collected_string += str(k + 1)
            else:
                collected_string += "_"
        return f"{item} ({collected_string})"

    def _track_kongs(self, player: Any, item: str) -> str:
        collected_string = ""
        for kong in KONGS:
            if player.has_item(kong):
                collected_string += kong[0]
            else:
                collected_string += "_"
        return f"{item} Kong ({collected_string})"

    # ------------------------------------------------------------------
    # Location tracking
    # ------------------------------------------------------------------

    def handle_location_tracking(
        self, game: Any, player: Any, item: Any, use_everywhere: bool = False
    ) -> Optional[str]:
        # DK64 doesn't have special location tracking (yet)
        return None

    # ------------------------------------------------------------------
    # Location hinting
    # ------------------------------------------------------------------

    def handle_location_hinting(
        self, player: Any, location: Any
    ) -> Optional[Tuple[List[str], str]]:
        # DK64 doesn't have special location hinting (yet)
        return None

    # ------------------------------------------------------------------
    # State tracking (goal string + stats)
    # ------------------------------------------------------------------

    def handle_state_tracking(self, player: Any, game: Any) -> None:
        settings = player.settings
        goal = settings["Goal"]

        dk64_goal = lambda string: (
            string
            + (
                ", then defeat King K. Rool"
                if settings.get("Require Beating K. Rool", False)
                else ""
            )
        )

        goal_str = self._build_goal_string(goal, settings, dk64_goal)
        player.stats.goal_str = goal_str

    def _build_goal_string(
        self, goal: str, settings: dict, dk64_goal
    ) -> str:
        match goal:
            case "Beat K Rool" | "Krool":
                keys_required = settings.get("Keys Required to Beat Krool", 0)
                prefix = (
                    f"Collect {keys_required} Keys, then "
                    if keys_required > 0
                    else ""
                )
                return prefix + "Defeat King K. Rool"

            case "All Keys":
                return dk64_goal("Collect all 8 Keys to K. Lumsy's Cage")

            case "Acquire Key 8":
                if settings.get("Lock Helm Key", False):
                    return dk64_goal(
                        "Break into Hideout Helm and obtain Key 8"
                    )
                else:
                    return dk64_goal("Obtain Key 8 for K. Lumsy's Cage")

            case "Kremling Kapture":
                return dk64_goal(
                    "Take a photo of every single enemy in the game"
                )

            case "DK Rap":
                return dk64_goal(
                    "Collect every item mentioned in the DK Rap"
                )

            case "Golden Bananas":
                required = settings["Goal Quantity"]["Golden Bananas"]
                return dk64_goal(f"Collect {required} Golden Bananas")

            case "Blueprints":
                required = settings["Goal Quantity"]["Blueprints"]
                return dk64_goal(f"Collect {required} Blueprints")

            case "Company Coins":
                required = settings["Goal Quantity"]["Company Coins"]
                if required == 1:
                    return dk64_goal(
                        "Find either the Nintendo or Rareware Company Coin"
                    )
                elif required == 2:
                    return dk64_goal(
                        "Find both the Nintendo and Rareware Company Coins"
                    )
                else:
                    return dk64_goal(f"Collect {required} Company Coins")

            case "Keys":
                required = settings["Goal Quantity"]["Keys"]
                return dk64_goal(f"Collect {required} Keys")

            case "Medals":
                required = settings["Goal Quantity"]["Medals"]
                return dk64_goal(f"Collect {required} Banana Medals")

            case "Crowns":
                required = settings["Goal Quantity"]["Crowns"]
                return dk64_goal(f"Collect {required} Battle Crowns")

            case "Fairies":
                required = settings["Goal Quantity"]["Fairies"]
                return dk64_goal(f"Rescue {required} Fairies")

            case "Rainbow Coins":
                required = settings["Goal Quantity"]["Rainbow Coins"]
                return dk64_goal(f"Collect {required} Rainbow Coins")

            case "Bean":
                return dk64_goal("Find The Bean")

            case "Pearls":
                required = settings["Goal Quantity"]["Pearls"]
                return dk64_goal(
                    f"Collect {required} Pearls for the Mermaid in Galleon"
                )

            case "Bosses":
                required = settings["Goal Quantity"]["Bosses"]
                return dk64_goal(f"Defeat {required} Bosses")

            case "Bonuses":
                required = settings["Goal Quantity"]["Bonuses"]
                return dk64_goal(f"Complete {required} Bonus Barrels")

            case "Treasure Hurry":
                return dk64_goal(
                    "Run down the clock by collecting treasure"
                )

            case "Krools Challenge":
                return (
                    "Defeat King K. Rool - but only after collecting all keys and blueprints, "
                    "defeating every other boss, and completing all bonus barrels"
                )

            case "Kill The Rabbit":
                return dk64_goal(
                    "Find your way to Chunky's Igloo in Crystal Caves, "
                    "then watch the rabbit get blown up (you monster)"
                )

            case _:
                return goal


# Module-level variable that __init__.py's auto-discovery looks for
handler = DK64Handler()
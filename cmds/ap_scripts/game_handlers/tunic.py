"""TUNIC — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "TUNIC"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class TUNICHandler:
    game = "TUNIC"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking", "handle_location_hinting"})

    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        treasures = {'DEF': ['Secret Legend', 'Phonomath'], 'POTION': ['Spring Falls', 'Just Some Pals', 'Back To Work'], 'SP': ['Forever Friend', 'Mr Mayor', 'Power Up', 'Regal Weasel'], 'MP': ['Sacred Geometry', 'Vintage', 'Dusty']}
        if item == 'Effigy':
            amts = [8, 16, 32, 64, 128, 256, 512]
            money_received: int
            if count > len(amts):
                money_received = amts[-1]
            else:
                money_received = amts[count - 1]
            return f'{item} ({money_received} Money)'
        if item == 'Flask Shard':
            flask_progress = player.get_item_count(item) % 3
            return f"{item} ({('Gained Flask!' if flask_progress == 0 else f'{flask_progress}/3')})"
        if item == 'Fairy':
            required = 20
            if count < 10:
                required = 10
            return f'{item} ({count}/{required})'
        if item == 'Gold Questagon':
            required = settings['Gold Hexagons Required']
            return f'{item} (*{count}/{required}*)'
        if item == 'Golden Coin':
            required = [3, 6, 10, 15, 20]
            next_req = 0
            for check in required:
                if count >= check:
                    continue
                if count < check:
                    next_req = check
                    break
            if next_req == 0:
                next_req = 20
            return f'{item} ({count}/{next_req})'
        if item in ['Blue Questagon', 'Red Questagon', 'Green Questagon']:
            count = len(player.get_collected_items(['Blue Questagon', 'Red Questagon', 'Green Questagon']))
            required = 3
            return f'{item} (*{count}/{required}*)'
        if item == 'Sword Upgrade':
            upgrades = ['Stick', "Ruin Seeker's Sword", "Librarian's Sword", "Heir's Sword"]
            upgrade = upgrades[count - 1]
            return f'{item} (LV{count}: {upgrade})'
        for stat, stat_items in treasures.items():
            if item in stat_items:
                return f'{item} (+1 {stat})'


    def handle_location_hinting(self, player, location):
        settings = player.settings
        slot_data = player.slot_data
        requirements = {}
        costs = {}
        extra_info = ""
        l = location
        location = location.name
        def add_requirement(text):
            from cmds.ap_scripts.requirements import parse_requirement_string

            parsed = parse_requirement_string(text)
            if parsed is None:
                requirements[str(text)] = None
            else:
                name, count = parsed
                prev = requirements.get(name)
                requirements[name] = count if prev is None else prev + count
        if location.startswith('Shop - Potion') or location.startswith('Shop - Coin'):
            price = 300
            add_requirement(f'{price} Money')
        return (requirements, extra_info, costs)


handler = TUNICHandler()

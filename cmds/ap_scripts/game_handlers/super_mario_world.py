"""Super Mario World — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Super Mario World"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class SuperMarioWorldHandler:
    game = "Super Mario World"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"handle_item_tracking"})

    def handle_item_tracking(self, itemlog, player, item):
        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        switches = [f'{color} Switch Palace' for color in ['Red', 'Green', 'Blue', 'Yellow']]
        if item == 'Progressive Powerup':
            prog_powerup = ['Super Mushroom', 'Fire Flower', 'Cape Feather']
            return f'{item} ({prog_powerup[count - 1]})'
        if item == 'Yoshi Egg' and settings['Goal'] == 'Yoshi Egg Hunt':
            required = round(settings['Max Number of Yoshi Eggs'] * (settings['Required Percentage of Yoshi Eggs'] / 100))
            return f'{item} ({count}/{required})'
        if item == 'Boss Token' and settings['Goal'] == 'Bowser':
            required = settings['Bosses Required']
            return f'{item} ({count}/{required})'
        if item in switches:
            switchcol = ''
            for s in switches:
                if player.has_item(s):
                    switchcol += s[0]
                else:
                    switchcol += '_'
            return f'{item} ({switchcol})'
        if item == 'Golden Yoshi Egg':
            total = settings['Yoshi Egg Count'] + len([l for l in player.spoilers['locations'] if l.name.endswith('(Hidden Egg)')])
            required = round(total * (settings['Required Percentage of Golden Yoshi Eggs'] / 100))
            return f'{item} (*{count}/{required}*)'
        if item == 'Progressive Swim' and game == 'SMW: Spicy Mycena Waffles':
            upgrades = ['Swimming', 'Carry Speed']
            return f'{item} ({upgrades[count - 1]})'
        if item == 'Progressive Run' and game == 'SMW: Spicy Mycena Waffles':
            if count == 2:
                return f'{item} (Wall Run)'

handler = SuperMarioWorldHandler()

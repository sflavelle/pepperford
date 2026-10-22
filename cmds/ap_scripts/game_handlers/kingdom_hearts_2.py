"""Kingdom Hearts 2 — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Kingdom Hearts 2"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class KingdomHearts2Handler:
    game = "Kingdom Hearts 2"
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
        world_unlocks = {"Naminé's Sketches": 'Simulated Twilight Town', 'Ice Cream': 'Twilight Town', 'Membership Card': 'Hollow Bastion', 'Sword of the Ancestor': 'Land of Dragons', "Beast's Claw": "Beast's Castle", 'Battlefields of War': 'Olympus', 'Disney Castle Key': 'Disney Castle', 'Skill and Crossbones': 'Port Royal', 'Scimitar': 'Agrabah', 'Bone Fist': 'Halloween Town', 'Proud Fang': 'Pride Lands', 'Identity Disk': 'Space Paranoids', 'Way to the Dawn': 'World that Never Was', 'Torn Page': 'Hundred Acre Wood'}
        if item == 'Bounty' and settings['Goal'] == 'Hitlist':
            required = settings['Bounties Required']
            return f'{item} (*{count}/{required}*)'
        if item in world_unlocks.keys():
            level = world_unlocks[item]
            if level == 'Disney Castle':
                if count == 2:
                    level = 'Timeless River'
                return f'{item} ({level} Unlocked!)'
            else:
                return f'{item} ({level} {count} Unlocked!)'

handler = KingdomHearts2Handler()

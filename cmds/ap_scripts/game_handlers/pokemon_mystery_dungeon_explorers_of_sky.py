"""Pokemon Mystery Dungeon Explorers of Sky — game-specific handling extracted from utils.handle_item_tracking.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check handle_item_tracking "Pokemon Mystery Dungeon Explorers of Sky"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class PokemonMysteryDungeonExplorersOfSkyHandler:
    game = "Pokemon Mystery Dungeon Explorers of Sky"
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
        sky_peaks = ['1st Station Pass', '2nd Station Pass', '3rd Station Pass', '4th Station Pass', '5th Station Pass', '6th Station Pass', '7th Station Pass', '8th Station Pass', '9th Station Pass', 'Sky Peak Summit Pass']
        instruments = ['Icy Flute', 'Fiery Drum', 'Terra Cymbal', 'Aqua-Monica', 'Rock Horn', 'Grass Corner', 'Sky Melodica', 'Stellar Symphony', 'Null Bagpipes', 'Glimmer Harp', 'Toxic Sax', 'Biting Bass', 'Knockout Bell', 'Spectral Chimes', "Liar's Lyre", 'Charge Synth', 'Norma-ccordion', 'Psychic Cello', 'Dragu-teki', 'Steel Guitar']
        seal_unlocks = ['Ice Aegis Cave', 'Rock Aegis Cave', 'Steel Aegis Cave', 'Aegis Cave Pit']
        if item == 'Progressive Sky Peak':
            return f'{item} ({sky_peaks[count - 1]})'

handler = PokemonMysteryDungeonExplorersOfSkyHandler()

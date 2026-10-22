"""gzDoom — game-specific handling extracted from utils.fetch_islocation_checkable.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check is_location_checkable "gzDoom"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class GzDoomHandler:
    game = "gzDoom"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"is_location_checkable", "handle_item_tracking"})

    def is_location_checkable(self, location):
        return True


    def handle_item_tracking(self, itemlog, player, item):
        # Imported lazily: utils owns these, and utils imports this package at module load.
        import re

        from cmds.ap_scripts.utils import gzd, item_table

        item = item.name
        settings = player.settings
        slot_data = player.slot_data
        spoiler = itemlog.spoiler_log[str(player)]
        count = player.get_item_count(item)
        game = player.game
        game_name = player.game
        item_regex = re.compile('^([a-zA-Z ]+?) \\((\\S+)\\)$')
        if item.startswith('Level Access'):
            item_match = item_regex.match(item)
            access, mapname = item_match.groups()
            friendly = gzd.lookupMap(wadname=settings['WAD to play'], mapname=mapname)
            if friendly is not None:
                logger.debug(f'Got friendly name for {mapname}: {friendly}')
                item = item.replace(mapname, f'{mapname}: {friendly}')
            else:
                logger.error(f"Couldn't lookup or find friendly name for {mapname}")
            count = len([i for i in player.inventory if str(i).startswith('Level Access')])
            total = len([i for i in player.spoilers['items'] if str(i).startswith('Level Access')])
            return f'{item} ({count}/{total})'
        if item.startswith('Level Clear'):
            count = len([i for i in player.inventory if str(i).startswith('Level Clear')])
            required_num = 0
            required_maps = []
            req_maps_formatted = []
            if settings['Win conditions']['nrof-maps'] == 'all':
                required_num = len(settings['Included levels'])
            else:
                if settings['Win conditions']['nrof-maps']:
                    required_num = int(settings['Win conditions']['nrof-maps'])
                if settings['Win conditions']['specific-maps']:
                    required_maps = list(settings['Win conditions']['specific-maps'])
            if len(required_maps) > 0:
                for map in required_maps:
                    if player.has_item(f'Level Clear ({map})'):
                        req_maps_formatted.append(f'~~{map}~~')
                    else:
                        req_maps_formatted.append(map)
            return f"""{item} ({count}/{required_num}{(f"+{','.join(req_maps_formatted)}" if len(required_maps) > 0 else '')})"""
        if any([str(item).startswith(color) for color in ['Blue', 'Yellow', 'Red']]) and (not str(item) == 'BlueArmor'):
            item_match = item_regex.match(item)
            subitem, map = item_match.groups()
            collected_string = str()
            keys = [f'{color}{key}' for color in ['Blue', 'Yellow', 'Red'] for key in ['Skull', 'Card']]
            map_keys = sorted([i for i in item_table['gzDoom'].keys() if i.endswith(f'({map})') and any([key in i for key in keys])])
            for i in map_keys:
                if player.has_item(i):
                    collected_string += i[0]
                else:
                    collected_string += '_'
            if not player.has_item(f'Level Access ({map})'):
                collected_string = f'~~{collected_string}~~'
            return f'{item} ({collected_string})'


handler = GzDoomHandler()

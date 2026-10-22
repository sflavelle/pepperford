"""APBingo — game-specific handling extracted from utils.fetch_islocation_checkable.

Behaviour is a verbatim move of the inline arm: no new behaviour, and the equivalence check
(`python -m cmds.ap_scripts.game_handlers._migrate check is_location_checkable "APBingo"`) runs the old arm
against this handler.
"""

from __future__ import annotations

import logging

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION

logger = logging.getLogger("ap_itemlog")


class APBingoHandler:
    game = "APBingo"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"is_location_checkable"})

    def is_location_checkable(self, location):
        return True

handler = APBingoHandler()

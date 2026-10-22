"""
Pokemon Black and White — classification of its `conditional progression` rows.

Moved out of `ap_itemlog.live_classification()`; the decision is the same, expressed as a
ClassificationProposal so the item log's own rules no longer carry game knowledge.
"""

from __future__ import annotations

from cmds.ap_scripts.game_handlers import PROTOCOL_VERSION, ClassificationProposal


class PokemonBlackAndWhiteHandler:
    game = "Pokemon Black and White"
    PROTOCOL_VERSION = PROTOCOL_VERSION
    implements = frozenset({"resolve_classification"})

    def resolve_classification(self, ctx, item, stored=None):
        setting = item.receiver.settings
        response = stored
        if item.name.startswith("TM") or item.name == "HM02 Fly":
            if setting["Goal"] in ("tmhm_hunt", "pokemon_master"):
                response = "progression"
            else:
                response = "useful"
        if response == stored:
            return None
        return ClassificationProposal(classification=response, reason="TM/HM moves only matter to a TM/HM goal")

handler = PokemonBlackAndWhiteHandler()

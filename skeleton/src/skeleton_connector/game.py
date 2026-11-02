"""Is the game running? The one question the recorder asks while no session plays.

REPLACE THIS FIRST. A real connector checks the game's process (or its window), never the
game's data channel: opening shared memory or a socket with the game closed costs CPU and can
even create the channel. The recorder calls :meth:`Game.running` at most every 2 s, so keep it
cheap, and it reads nothing else from the game until this says yes.

The skeleton has no game. Its fake: the file ``<workspace>/connectors/<id>/fake_game`` exists.
Create it to start a session, delete it to close the game.
"""

from __future__ import annotations

from pathlib import Path

from . import CONNECTOR_ID


class Game:
    def __init__(self, workspace: str | Path) -> None:
        self.flag = Path(workspace) / "connectors" / CONNECTOR_ID / "fake_game"

    def running(self) -> bool:
        return self.flag.exists()

"""A minimal Stintmate connector: the smallest program the hub and Pitwall accept as one.

It reads no simulator. Its "recorder" plays a short synthetic session (a garage exit, an out
lap, flying laps, a pit stop, a second stint) so every message an author must get right is
here in its simplest form. Replace :mod:`skeleton_connector.game` first (is the game running?),
then :mod:`skeleton_connector.session` (what a session of your simulator holds); keep the rest.
"""

import os
import re

from stintmate_ipc import pipe_path, pipe_user

CONNECTOR_ID = "skeleton"                 # folder name, hello meta, endpoint suffix
CONNECTOR_NAME = "Skeleton connector"
CONNECTOR_VERSION = "1.0.0"               # keep equal to pyproject.toml
SIM = "skeletonsim"                       # ^[a-z0-9_-]{1,32}$
SIM_NAME = "Skeleton Simulator"
APP_ID = f"connector-{CONNECTOR_ID}"      # peer.app, and the control endpoint's id
CONTROL_APP = "skeleton-connector"        # control.state.app
MUTEX = r"Local\SkeletonConnector.Control"
SERVICE_ID = "recorder"


def dev_tag() -> str | None:
    """``STINTMATE_DEV_INSTANCE``: a tag that separates a test run from a real Stintmate on the
    same machine (docs/guide.md section 3). Lowercase ``a-z``, ``0-9`` and ``-``, at most 24
    characters; None when unset or empty. Read here and nowhere else."""
    tag = re.sub(r"[^a-z0-9-]", "", os.environ.get("STINTMATE_DEV_INSTANCE", "").lower())[:24]
    return tag or None


def pipe(app: str) -> str:
    """The pipe path of an app's endpoint, with the tag in the user part when there is one."""
    tag = dev_tag()
    return pipe_path(app, user=f"{pipe_user()}-{tag}") if tag else pipe_path(app)

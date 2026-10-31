"""A minimal Stintmate connector: the smallest program the hub and Pitwall accept as one.

It reads no simulator. Its "recorder" plays a short synthetic session (a garage exit, an out
lap, flying laps, a pit stop, a second stint) so every message an author must get right is
here in its simplest form. Replace :mod:`skeleton_connector.recorder`'s ``play`` with the
reading of your own simulator; keep the rest.
"""

CONNECTOR_ID = "skeleton"                 # folder name, hello meta, endpoint suffix
CONNECTOR_NAME = "Skeleton connector"
CONNECTOR_VERSION = "1.0.0"               # keep equal to pyproject.toml
SIM = "skeletonsim"                       # ^[a-z0-9_-]{1,32}$
SIM_NAME = "Skeleton Simulator"
APP_ID = f"connector-{CONNECTOR_ID}"      # peer.app, and the control endpoint's id
CONTROL_APP = "skeleton-connector"        # control.state.app
MUTEX = r"Local\SkeletonConnector.Control"
SERVICE_ID = "recorder"

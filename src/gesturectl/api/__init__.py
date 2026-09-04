"""The contract between the interface and the machinery.

Nothing in this package may mention MediaPipe, OpenCV, a Roku key name or an IP
address in its public shape. If a new endpoint needs one of those, it belongs on
the wrong side of the line.
"""

from .app import create_app

__all__ = ["create_app"]

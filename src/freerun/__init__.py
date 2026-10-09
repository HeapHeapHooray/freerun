"""Freerun - Ultra-fast GPU Piano Visualizer."""

from freerun.cli import main
from freerun.renderer import FreerunRenderer
from freerun.geometry import PianoLayout
from freerun.midi import parse_midi
from freerun.encoder import VideoEncoder
from freerun.colors import ColorScheme

__version__ = "0.1.0"
__all__ = [
    "main",
    "FreerunRenderer",
    "PianoLayout",
    "parse_midi",
    "VideoEncoder",
    "ColorScheme",
]

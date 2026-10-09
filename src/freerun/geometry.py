"""Piano geometry, key layout calculations, and coordinate projections."""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, List, Tuple
import numpy as np

WHITE_KEY_SEMITONES = {0, 2, 4, 5, 7, 9, 11}  # C, D, E, F, G, A, B


@dataclass
class KeyGeom:
    pitch: int
    is_white: bool
    x_ndc: float
    w_ndc: float
    note_x_ndc: float
    note_w_ndc: float


class PianoLayout:
    """Calculates geometric positions for piano keys and falling notes in NDC [-1, 1]."""

    def __init__(
        self,
        min_pitch: int = 21,  # A0
        max_pitch: int = 108,  # C8
        keyboard_height_ratio: float = 0.15,  # 15% of screen height
    ):
        # Ensure bounds start and end on white keys for clean visual edges
        while min_pitch > 0 and (min_pitch % 12) not in WHITE_KEY_SEMITONES:
            min_pitch -= 1
        while max_pitch < 127 and (max_pitch % 12) not in WHITE_KEY_SEMITONES:
            max_pitch += 1

        self.min_pitch = min_pitch
        self.max_pitch = max_pitch
        self.keyboard_height_ratio = keyboard_height_ratio

        # NDC vertical geometry:
        # Keyboard sits at bottom: from -1.0 to y_hit_ndc
        # y_hit_ndc: -1.0 + (keyboard_height_ratio * 2.0)
        self.y_hit_ndc = -1.0 + (keyboard_height_ratio * 2.0)
        self.wk_height_ndc = self.y_hit_ndc - (-1.0)
        self.bk_height_ndc = self.wk_height_ndc * 0.63

        # Count white keys in range
        white_pitches: List[int] = []
        for p in range(min_pitch, max_pitch + 1):
            if (p % 12) in WHITE_KEY_SEMITONES:
                white_pitches.append(p)

        self.num_white_keys = len(white_pitches)
        self.wk_width_ndc = 2.0 / self.num_white_keys
        self.bk_width_ndc = self.wk_width_ndc * 0.62

        # Map each white key to its sequential index
        white_pitch_to_idx = {p: i for i, p in enumerate(white_pitches)}

        self.keys: Dict[int, KeyGeom] = {}
        # Precomputed fast lookup arrays for pitch 0..127
        self.pitch_to_note_x = np.zeros(128, dtype=np.float32)
        self.pitch_to_note_w = np.zeros(128, dtype=np.float32)
        self.pitch_to_center_x = np.zeros(128, dtype=np.float32)
        self.pitch_is_white = np.zeros(128, dtype=bool)
        self.pitch_in_range = np.zeros(128, dtype=bool)

        w_idx = 0
        for p in range(min_pitch, max_pitch + 1):
            is_white = (p % 12) in WHITE_KEY_SEMITONES
            if is_white:
                x = -1.0 + w_idx * self.wk_width_ndc
                w = self.wk_width_ndc
                w_idx += 1
            else:
                # Black key sits between adjacent white keys (w_idx - 1 and w_idx)
                boundary_x = -1.0 + w_idx * self.wk_width_ndc
                x = boundary_x - self.bk_width_ndc * 0.5
                w = self.bk_width_ndc

            note_x = x + w * 0.05
            note_w = w * 0.90
            center_x = x + w * 0.5

            self.keys[p] = KeyGeom(
                pitch=p,
                is_white=is_white,
                x_ndc=x,
                w_ndc=w,
                note_x_ndc=note_x,
                note_w_ndc=note_w,
            )

            self.pitch_to_note_x[p] = note_x
            self.pitch_to_note_w[p] = note_w
            self.pitch_to_center_x[p] = center_x
            self.pitch_is_white[p] = is_white
            self.pitch_in_range[p] = True

        # Precompute static keyboard instance arrays for white and black keys
        wk_list: List[Tuple[float, float, float, float, int]] = []
        bk_list: List[Tuple[float, float, float, float, int]] = []

        for p in range(min_pitch, max_pitch + 1):
            k = self.keys[p]
            if k.is_white:
                # rect: x, y, w, h, pitch
                wk_list.append((k.x_ndc, -1.0, k.w_ndc, self.wk_height_ndc, p))
            else:
                y = self.y_hit_ndc - self.bk_height_ndc
                bk_list.append((k.x_ndc, y, k.w_ndc, self.bk_height_ndc, p))

        self.white_keys_static = wk_list
        self.black_keys_static = bk_list

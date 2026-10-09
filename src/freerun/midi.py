"""Fast, robust MIDI parsing and tempo-map conversion for Freerun."""

from __future__ import annotations
import bisect
from dataclasses import dataclass
from typing import List, Tuple, Dict
import mido
import numpy as np

from freerun.colors import ColorScheme


@dataclass
class Note:
    pitch: int
    start: float
    end: float
    velocity: int
    track: int
    channel: int


@dataclass
class MidiTrackInfo:
    notes: List[Note]
    duration: float
    min_pitch: int
    max_pitch: int
    track_count: int
    # NumPy vectorized arrays for ultra-fast frame slicing
    starts_np: np.ndarray
    ends_np: np.ndarray
    pitches_np: np.ndarray
    velocities_np: np.ndarray
    tracks_np: np.ndarray
    colors_np: np.ndarray  # shape (N, 4) float32 RGBA


def parse_midi(midi_path: str, color_scheme: ColorScheme) -> MidiTrackInfo:
    """Parses a MIDI file into Note objects and vectorized NumPy arrays."""
    mid = mido.MidiFile(midi_path)
    tpb = mid.ticks_per_beat

    # 1. Collect all tempo changes to build tempo map
    tempo_changes: List[Tuple[int, int]] = [(0, 500000)]  # (tick, tempo_us_per_beat)
    for track in mid.tracks:
        cur_tick = 0
        for msg in track:
            cur_tick += msg.time
            if msg.type == "set_tempo":
                tempo_changes.append((cur_tick, msg.tempo))

    tempo_changes.sort(key=lambda x: x[0])
    # Deduplicate consecutive tempos at identical ticks
    unique_tempos: List[Tuple[int, int]] = []
    for tick, tempo in tempo_changes:
        if unique_tempos and unique_tempos[-1][0] == tick:
            unique_tempos[-1] = (tick, tempo)
        else:
            unique_tempos.append((tick, tempo))

    # Compute cumulative seconds at each tempo breakpoint
    tempo_map: List[Tuple[int, int, float]] = []  # (tick, tempo, cumulative_time)
    cum_time = 0.0
    last_tick = 0
    last_tempo = 500000
    for tick, tempo in unique_tempos:
        cum_time += (tick - last_tick) * (last_tempo / (tpb * 1e6))
        tempo_map.append((tick, tempo, cum_time))
        last_tick = tick
        last_tempo = tempo

    ticks_list = [x[0] for x in tempo_map]

    def tick_to_seconds(tick: int) -> float:
        idx = bisect.bisect_right(ticks_list, tick) - 1
        t_tick, tempo, t_cum = tempo_map[idx]
        return t_cum + (tick - t_tick) * (tempo / (tpb * 1e6))

    # 2. Extract notes from each track
    notes: List[Note] = []
    for track_idx, track in enumerate(mid.tracks):
        cur_tick = 0
        # active notes: (channel, pitch) -> (start_tick, velocity)
        active_notes: Dict[Tuple[int, int], Tuple[int, int]] = {}

        for msg in track:
            cur_tick += msg.time
            if msg.type == "note_on" and msg.velocity > 0:
                key = (msg.channel, msg.note)
                if key in active_notes:
                    s_tick, vel = active_notes[key]
                    s_time = tick_to_seconds(s_tick)
                    e_time = max(s_time + 0.02, tick_to_seconds(cur_tick))
                    notes.append(
                        Note(
                            pitch=msg.note,
                            start=s_time,
                            end=e_time,
                            velocity=vel,
                            track=track_idx,
                            channel=msg.channel,
                        )
                    )
                active_notes[key] = (cur_tick, msg.velocity)
            elif (msg.type == "note_off") or (msg.type == "note_on" and msg.velocity == 0):
                key = (msg.channel, msg.note)
                if key in active_notes:
                    s_tick, vel = active_notes.pop(key)
                    s_time = tick_to_seconds(s_tick)
                    e_time = max(s_time + 0.02, tick_to_seconds(cur_tick))
                    notes.append(
                        Note(
                            pitch=msg.note,
                            start=s_time,
                            end=e_time,
                            velocity=vel,
                            track=track_idx,
                            channel=msg.channel,
                        )
                    )

        # Flush any remaining unclosed notes
        for (ch, pitch), (s_tick, vel) in active_notes.items():
            s_time = tick_to_seconds(s_tick)
            e_time = max(s_time + 0.02, tick_to_seconds(cur_tick))
            notes.append(
                Note(
                    pitch=pitch,
                    start=s_time,
                    end=e_time,
                    velocity=vel,
                    track=track_idx,
                    channel=ch,
                )
            )

    if not notes:
        raise ValueError(f"No MIDI notes found in {midi_path}")

    # Sort notes strictly by start time
    notes.sort(key=lambda n: (n.start, n.pitch))

    duration = max(n.end for n in notes)
    min_pitch = min(n.pitch for n in notes)
    max_pitch = max(n.pitch for n in notes)

    # 3. Create high-performance NumPy arrays
    n_count = len(notes)
    starts_np = np.zeros(n_count, dtype=np.float32)
    ends_np = np.zeros(n_count, dtype=np.float32)
    pitches_np = np.zeros(n_count, dtype=np.int32)
    velocities_np = np.zeros(n_count, dtype=np.float32)
    tracks_np = np.zeros(n_count, dtype=np.int32)
    colors_np = np.zeros((n_count, 4), dtype=np.float32)

    for i, n in enumerate(notes):
        starts_np[i] = n.start
        ends_np[i] = n.end
        pitches_np[i] = n.pitch
        velocities_np[i] = n.velocity / 127.0
        tracks_np[i] = n.track
        rgba = color_scheme.get_color(n.pitch, n.track, n.channel, n.velocity)
        colors_np[i] = rgba

    return MidiTrackInfo(
        notes=notes,
        duration=duration,
        min_pitch=min_pitch,
        max_pitch=max_pitch,
        track_count=len(mid.tracks),
        starts_np=starts_np,
        ends_np=ends_np,
        pitches_np=pitches_np,
        velocities_np=velocities_np,
        tracks_np=tracks_np,
        colors_np=colors_np,
    )

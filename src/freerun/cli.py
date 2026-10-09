"""Command-line interface and main rendering orchestrator for Freerun."""

from __future__ import annotations
import argparse
import os
import sys
import time
from typing import Optional
from tqdm import tqdm

from freerun.colors import ColorScheme, PALETTES
from freerun.encoder import VideoEncoder, get_audio_duration, detect_best_encoder
from freerun.geometry import PianoLayout
from freerun.midi import parse_midi
from freerun.overlay import auto_detect_metadata
from freerun.renderer import FreerunRenderer


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="freerun",
        description="Freerun - Ultra-fast GPU-accelerated Piano Visualizer (MP3 + MIDI -> MP4)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    # Inputs and outputs (can be positional or flagged)
    parser.add_argument("audio_pos", nargs="?", default=None, help="Path to input audio file (.mp3, .wav, etc.)")
    parser.add_argument("midi_pos", nargs="?", default=None, help="Path to input MIDI file (.mid, .midi)")
    parser.add_argument("output_pos", nargs="?", default=None, help="Path to output video file (.mp4)")

    parser.add_argument("-a", "--audio", dest="audio_opt", default=None, help="Path to input audio file")
    parser.add_argument("-m", "--midi", dest="midi_opt", default=None, help="Path to input MIDI file")
    parser.add_argument("-o", "--output", dest="output_opt", default=None, help="Output MP4 file path")

    # Title & Metadata Overlay
    title_group = parser.add_argument_group("On-Screen Title & Instrument Badge")
    title_group.add_argument("-t", "--title", "--name", "--song", dest="title", default=None, help="Music title to display on screen")
    title_group.add_argument("-i", "--instruments", "--instrument", dest="instruments", default=None, help="Instrument(s) used (e.g. 'Grand Piano' or 'Piano, Strings')")
    title_group.add_argument("--artist", default=None, help="Artist or composer name")
    title_group.add_argument("--title-duration", type=float, default=7.0, help="Duration (seconds) to display title card (0 for persistent)")
    title_group.add_argument("--persistent-title", action="store_true", help="Keep title badge visible throughout entire video")
    title_group.add_argument("--no-title", action="store_true", help="Disable the title card badge overlay")

    # Resolution & Frame Rate
    video_group = parser.add_argument_group("Video Settings")
    video_group.add_argument("-w", "--width", type=int, default=1920, help="Video width in pixels")
    video_group.add_argument("-H", "--height", type=int, default=1080, help="Video height in pixels")
    video_group.add_argument("-r", "--fps", type=int, default=60, help="Frames per second")
    video_group.add_argument("--720p", dest="res_720p", action="store_true", help="Set resolution to 1280x720")
    video_group.add_argument("--1080p", dest="res_1080p", action="store_true", help="Set resolution to 1920x1080")
    video_group.add_argument("--4k", dest="res_4k", action="store_true", help="Set resolution to 3840x2160")

    # Timing & Sync
    timing_group = parser.add_argument_group("Timing & Synchronization")
    timing_group.add_argument(
        "--lead-in",
        type=float,
        default=2.5,
        help="Lead-in time (seconds) before notes hit keyboard, allowing notes to fall smoothly from top",
    )
    timing_group.add_argument(
        "--fall-time",
        type=float,
        default=2.5,
        help="Time (seconds) for a note to fall from top of screen to the piano keys",
    )
    timing_group.add_argument(
        "--audio-offset",
        type=float,
        default=0.0,
        help="Fine audio offset adjustment in seconds (+ delay audio, - advance audio)",
    )
    timing_group.add_argument(
        "--duration",
        type=float,
        default=None,
        help="Override total video duration in seconds (defaults to audio / MIDI length)",
    )

    # Visual Appearance
    style_group = parser.add_argument_group("Visual Styling")
    style_group.add_argument(
        "-c",
        "--color-scheme",
        default="neon",
        choices=["neon", "synthwave", "rainbow", "sunset", "ocean", "emerald", "fire", "tracks"],
        help="Color palette preset",
    )
    style_group.add_argument("--color-left", default=None, help="Custom hex color for left hand / track 0 (e.g. #00f2fe)")
    style_group.add_argument("--color-right", default=None, help="Custom hex color for right hand / track 1 (e.g. #ff0844)")
    style_group.add_argument("--keyboard-height", type=float, default=0.15, help="Keyboard height as a fraction of screen")
    style_group.add_argument("--auto-keys", action="store_true", help="Auto-zoom keyboard to range of notes played")
    style_group.add_argument("--min-key", type=int, default=21, help="Lowest piano key (MIDI pitch, 21=A0)")
    style_group.add_argument("--max-key", type=int, default=108, help="Highest piano key (MIDI pitch, 108=C8)")
    style_group.add_argument("--no-particles", dest="particles", action="store_false", default=True, help="Disable hit sparks")
    style_group.add_argument("--no-glow", dest="glow", action="store_false", default=True, help="Disable bloom flare glow")

    # Encoding
    encoder_group = parser.add_argument_group("Encoder Settings")
    encoder_group.add_argument(
        "--encoder",
        default="auto",
        choices=["auto", "vaapi", "nvenc", "qsv", "libx264"],
        help="Video encoder (auto detects GPU hardware acceleration)",
    )
    encoder_group.add_argument("--crf", type=int, default=18, help="Quality / CRF level (lower is higher quality)")
    encoder_group.add_argument("--preset", default="ultrafast", help="Preset for libx264 (ultrafast, superfast, etc.)")

    args = parser.parse_args(argv)

    # Resolution shortcuts
    if args.res_720p:
        args.width, args.height = 1280, 720
    elif args.res_1080p:
        args.width, args.height = 1920, 1080
    elif args.res_4k:
        args.width, args.height = 3840, 2160

    # Consolidate audio & midi inputs
    audio_path = args.audio_opt or args.audio_pos
    midi_path = args.midi_opt or args.midi_pos

    # If inputs were swapped by user (e.g. freerun song.mid song.mp3)
    if audio_path and (audio_path.lower().endswith(".mid") or audio_path.lower().endswith(".midi")):
        if midi_path and (midi_path.lower().endswith(".mp3") or midi_path.lower().endswith(".wav")):
            audio_path, midi_path = midi_path, audio_path

    # Output path
    output_path = args.output_opt or args.output_pos
    if not output_path and midi_path:
        base_name = os.path.splitext(os.path.basename(midi_path))[0]
        output_path = f"{base_name}_visualizer.mp4"

    args.audio_file = audio_path
    args.midi_file = midi_path
    args.output_file = output_path

    return args


def run():
    args = parse_args()

    # Validation
    if not args.midi_file:
        print("Error: MIDI file is required. Usage: freerun <audio.mp3> <song.mid> [output.mp4]", file=sys.stderr)
        sys.exit(1)

    if not os.path.exists(args.midi_file):
        print(f"Error: MIDI file not found: {args.midi_file}", file=sys.stderr)
        sys.exit(1)

    if args.audio_file and not os.path.exists(args.audio_file):
        print(f"Error: Audio file not found: {args.audio_file}", file=sys.stderr)
        sys.exit(1)

    # Ensure even dimensions for video codecs
    width = (args.width // 2) * 2
    height = (args.height // 2) * 2

    # Determine Title and Instruments for On-Screen Overlay
    title_to_show = None
    instruments_to_show = None
    artist_to_show = args.artist

    if not args.no_title:
        title_to_show = args.title
        instruments_to_show = args.instruments

        if not title_to_show or not instruments_to_show:
            auto_t, auto_art, auto_inst = auto_detect_metadata(args.audio_file, args.midi_file)
            if not title_to_show and auto_t:
                title_to_show = auto_t
            if not artist_to_show and auto_art:
                artist_to_show = auto_art
            if not instruments_to_show and auto_inst:
                instruments_to_show = auto_inst

    title_duration = 0.0 if args.persistent_title else args.title_duration

    print("=" * 64)
    print("  FREERUN - Ultra-fast GPU Piano Visualizer")
    print("=" * 64)
    print(f" • MIDI File      : {args.midi_file}")
    print(f" • Audio File     : {args.audio_file or 'None'}")
    print(f" • Output File    : {args.output_file}")
    print(f" • Resolution     : {width}x{height} @ {args.fps} FPS")
    print(f" • Palette        : {args.color_scheme}")
    if title_to_show:
        print(f" • Title          : {title_to_show}")
    if artist_to_show:
        print(f" • Artist         : {artist_to_show}")
    if instruments_to_show:
        print(f" • Instrument(s)  : {instruments_to_show}")

    # 1. Parse MIDI
    print("\n[1/4] Parsing MIDI file...")
    color_scheme = ColorScheme(
        name=args.color_scheme,
        custom_left=args.color_left,
        custom_right=args.color_right,
        min_pitch=args.min_key,
        max_pitch=args.max_key,
    )
    t_start_midi = time.perf_counter()
    midi_info = parse_midi(args.midi_file, color_scheme)
    t_midi = time.perf_counter() - t_start_midi
    print(
        f"      Found {len(midi_info.notes):,} notes across {midi_info.track_count} tracks "
        f"(MIDI duration: {midi_info.duration:.2f}s, parsed in {t_midi*1000:.1f}ms)"
    )

    # 2. Determine Keyboard Layout
    if args.auto_keys:
        min_pitch = max(0, midi_info.min_pitch - 2)
        max_pitch = min(127, midi_info.max_pitch + 2)
    else:
        min_pitch = args.min_key
        max_pitch = args.max_key

    layout = PianoLayout(
        min_pitch=min_pitch,
        max_pitch=max_pitch,
        keyboard_height_ratio=args.keyboard_height,
    )
    print(f"      Piano Range: {layout.min_pitch} to {layout.max_pitch} ({layout.num_white_keys} white keys)")

    # 3. Determine Duration & Sync
    audio_dur = get_audio_duration(args.audio_file) if args.audio_file else None
    if args.duration is not None:
        content_duration = args.duration
    elif audio_dur is not None:
        content_duration = max(audio_dur, midi_info.duration)
    else:
        content_duration = midi_info.duration + 1.0

    lead_in = max(0.0, args.lead_in)
    total_duration = lead_in + content_duration
    total_frames = int(round(total_duration * args.fps))

    print(f"\n[2/4] Timing & Audio Sync:")
    print(f"      Lead-in: {lead_in:.2f}s | Audio duration: {audio_dur or 'N/A'}")
    print(f"      Total video duration: {total_duration:.2f}s ({total_frames:,} frames)")

    # 4. Initialize ModernGL Renderer
    print(f"\n[3/4] Initializing GPU Renderer...")
    t_init = time.perf_counter()
    renderer = FreerunRenderer(
        width=width,
        height=height,
        layout=layout,
        midi_info=midi_info,
        fall_duration=args.fall_time,
        lead_in=lead_in,
        enable_particles=args.particles,
        enable_glow=args.glow,
        title=title_to_show,
        instruments=instruments_to_show,
        artist=artist_to_show,
        title_duration=title_duration,
        accent_color=color_scheme.get_color(60)[:3],
    )
    print(f"      Renderer ready: {renderer.ctx.info.get('GL_RENDERER', 'OpenGL')} ({time.perf_counter() - t_init:.2f}s)")

    # 5. Initialize Video Encoder
    encoder_choice = args.encoder
    if encoder_choice == "auto":
        detected = detect_best_encoder()
        print(f"      Auto-detected encoder: {detected}")
        encoder_choice = detected

    audio_delay = lead_in + args.audio_offset
    encoder = VideoEncoder(
        output_path=args.output_file,
        width=width,
        height=height,
        fps=args.fps,
        audio_path=args.audio_file,
        audio_delay=audio_delay,
        encoder=encoder_choice,
        crf=args.crf,
        preset=args.preset,
    )
    print(f"      Encoder pipeline initialized: {encoder.encoder.upper()}")

    # 6. Render & Encode Loop
    print(f"\n[4/4] Rendering & Encoding video...")
    dt = 1.0 / float(args.fps)
    start_time = time.perf_counter()

    pbar = tqdm(
        total=total_frames,
        unit="frame",
        dynamic_ncols=True,
        bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}{postfix}]",
    )

    try:
        for frame_idx in range(total_frames):
            # t_video: current playback time relative to content
            # At frame 0: t = -lead_in
            # At frame (lead_in * fps): t = 0.0 (notes hit hitline!)
            t_current = -lead_in + (frame_idx * dt)

            # GPU Render pass
            frame_bytes = renderer.render_frame(t_current, dt)

            # Push to threaded async encoder
            encoder.write_frame(frame_bytes)

            pbar.update(1)
            if frame_idx % 30 == 0 and frame_idx > 0:
                elapsed = time.perf_counter() - start_time
                curr_fps = frame_idx / elapsed
                rt_speed = (frame_idx * dt) / elapsed
                pbar.set_postfix_str(f"{curr_fps:.1f} fps ({rt_speed:.2f}x)")
    finally:
        pbar.close()
        encoder.close()

    total_time = time.perf_counter() - start_time
    avg_fps = total_frames / max(0.001, total_time)
    realtime_ratio = total_duration / max(0.001, total_time)

    out_size_mb = os.path.getsize(args.output_file) / (1024 * 1024) if os.path.exists(args.output_file) else 0

    print("\n" + "=" * 64)
    print("  RENDER COMPLETE!")
    print("=" * 64)
    print(f" • Total Frames    : {total_frames:,}")
    print(f" • Render Time     : {total_time:.2f} seconds")
    print(f" • Average Speed   : {avg_fps:.1f} FPS ({realtime_ratio:.2f}x realtime)")
    print(f" • Output File     : {args.output_file} ({out_size_mb:.2f} MB)")
    print("=" * 64 + "\n")


def main():
    try:
        run()
    except KeyboardInterrupt:
        print("\nRendering interrupted by user.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

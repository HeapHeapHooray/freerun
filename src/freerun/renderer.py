"""ModernGL GPU renderer for Freerun piano visualizer."""

from __future__ import annotations
import bisect
from typing import Tuple, Dict
import moderngl
import numpy as np

from freerun.geometry import PianoLayout
from freerun.midi import MidiTrackInfo
from freerun.particles import ParticleSystem
from freerun.overlay import TitleCardOverlay
from freerun import shaders


class FreerunRenderer:
    """Ultra-fast GPU piano roll visualizer renderer using ModernGL."""

    def __init__(
        self,
        width: int = 1920,
        height: int = 1080,
        layout: PianoLayout | None = None,
        midi_info: MidiTrackInfo | None = None,
        fall_duration: float = 2.5,
        lead_in: float = 2.5,
        enable_particles: bool = True,
        enable_glow: bool = True,
        title: str | None = None,
        instruments: str | None = None,
        artist: str | None = None,
        title_duration: float = 7.0,
        accent_color: Tuple[float, float, float] = (0.0, 0.95, 1.0),
    ):
        self.width = width
        self.height = height
        self.layout = layout or PianoLayout()
        self.midi = midi_info
        self.fall_duration = fall_duration
        self.lead_in = lead_in
        self.enable_particles = enable_particles
        self.enable_glow = enable_glow

        # Initialize ModernGL standalone context
        self.ctx = moderngl.create_context(standalone=True)
        self.fbo = self.ctx.simple_framebuffer((self.width, self.height))
        self.fbo.use()

        # Shared Unit Quad: (0, 0) to (1, 1) triangle strip
        quad_data = np.array([
            0.0, 0.0,
            1.0, 0.0,
            0.0, 1.0,
            1.0, 1.0,
        ], dtype=np.float32)
        self.quad_vbo = self.ctx.buffer(quad_data.tobytes())

        # Centered Unit Quad for particles and flares: [-1, 1]
        centered_quad_data = np.array([
            -1.0, -1.0,
             1.0, -1.0,
            -1.0,  1.0,
             1.0,  1.0,
        ], dtype=np.float32)
        self.centered_quad_vbo = self.ctx.buffer(centered_quad_data.tobytes())

        # Compile Shader Programs
        self.bg_prog = self.ctx.program(
            vertex_shader=shaders.BG_VERTEX_SHADER,
            fragment_shader=shaders.BG_FRAGMENT_SHADER,
        )
        self.wk_prog = self.ctx.program(
            vertex_shader=shaders.WK_VERTEX_SHADER,
            fragment_shader=shaders.WK_FRAGMENT_SHADER,
        )
        self.bk_prog = self.ctx.program(
            vertex_shader=shaders.BK_VERTEX_SHADER,
            fragment_shader=shaders.BK_FRAGMENT_SHADER,
        )
        self.notes_prog = self.ctx.program(
            vertex_shader=shaders.NOTES_VERTEX_SHADER,
            fragment_shader=shaders.NOTES_FRAGMENT_SHADER,
        )
        self.flare_prog = self.ctx.program(
            vertex_shader=shaders.FLARE_VERTEX_SHADER,
            fragment_shader=shaders.FLARE_FRAGMENT_SHADER,
        )
        self.spark_prog = self.ctx.program(
            vertex_shader=shaders.SPARK_VERTEX_SHADER,
            fragment_shader=shaders.SPARK_FRAGMENT_SHADER,
        )
        self.hitbar_prog = self.ctx.program(
            vertex_shader=shaders.HITBAR_VERTEX_SHADER,
            fragment_shader=shaders.HITBAR_FRAGMENT_SHADER,
        )

        # Uniforms
        res = (float(self.width), float(self.height))
        self.wk_prog["u_resolution"].value = res
        self.bk_prog["u_resolution"].value = res
        self.notes_prog["u_resolution"].value = res

        hit_y_uv = (self.layout.y_hit_ndc + 1.0) / 2.0
        self.bg_prog["u_hit_y"].value = hit_y_uv
        self.bg_prog["u_num_white_keys"].value = float(self.layout.num_white_keys)

        self.bar_height_ndc = 6.0 / (self.height * 0.5)  # ~6px bar
        self.hitbar_prog["u_hit_y_ndc"].value = self.layout.y_hit_ndc
        self.hitbar_prog["u_bar_height_ndc"].value = self.bar_height_ndc

        # VAO: Background & Hitbar
        self.bg_vao = self.ctx.vertex_array(self.bg_prog, [(self.quad_vbo, "2f", "in_vert")])
        self.hitbar_vao = self.ctx.vertex_array(self.hitbar_prog, [(self.quad_vbo, "2f", "in_vert")])

        # Keyboard Static Geometry & Buffers
        self._init_keyboard_buffers()

        # Dynamic Falling Notes Buffers
        self.max_notes_instances = 6000
        # 4f (rect) + 4f (color) + 4f (params) = 12 floats = 48 bytes per instance
        self.notes_buffer = np.zeros((self.max_notes_instances, 12), dtype=np.float32)
        self.notes_vbo = self.ctx.buffer(reserve=self.notes_buffer.nbytes, dynamic=True)
        self.notes_vao = self.ctx.vertex_array(
            self.notes_prog,
            [
                (self.quad_vbo, "2f", "in_vert"),
                (self.notes_vbo, "4f 4f 4f/i", "in_rect", "in_color", "in_params"),
            ],
        )

        # Dynamic Flares Buffers
        self.max_flares = 128
        self.flare_buffer = np.zeros((self.max_flares, 8), dtype=np.float32)
        self.flare_vbo = self.ctx.buffer(reserve=self.flare_buffer.nbytes, dynamic=True)
        self.flare_vao = self.ctx.vertex_array(
            self.flare_prog,
            [
                (self.quad_vbo, "2f", "in_vert"),
                (self.flare_vbo, "4f 4f/i", "in_rect", "in_color"),
            ],
        )

        # Dynamic Particles
        self.particles = ParticleSystem(
            max_particles=800,
            aspect_ratio=float(self.width) / float(self.height),
        )
        self.spark_vbo = self.ctx.buffer(reserve=self.particles.instance_buffer.nbytes, dynamic=True)
        self.spark_vao = self.ctx.vertex_array(
            self.spark_prog,
            [
                (self.centered_quad_vbo, "2f", "in_vert"),
                (self.spark_vbo, "4f 4f/i", "in_pos_size", "in_color"),
            ],
        )

        # Title and Instrument Card Overlay
        self.title_overlay = TitleCardOverlay(
            ctx=self.ctx,
            screen_width=self.width,
            screen_height=self.height,
            title=title,
            instruments=instruments,
            artist=artist,
            accent_color=accent_color,
            duration=title_duration,
        )

        # Fast search state for notes
        self.last_search_idx = 0

    def _init_keyboard_buffers(self):
        """Initializes white and black key instance buffers."""
        # White Keys
        num_wk = len(self.layout.white_keys_static)
        # Rect(4f) + Color(4f) + Pressed(1f) = 9 floats
        self.wk_data = np.zeros((num_wk, 9), dtype=np.float32)
        self.wk_pitches = np.zeros(num_wk, dtype=np.int32)
        for i, (x, y, w, h, p) in enumerate(self.layout.white_keys_static):
            self.wk_data[i, 0:4] = [x, y, w, h]
            self.wk_pitches[i] = p

        self.wk_vbo = self.ctx.buffer(reserve=self.wk_data.nbytes, dynamic=True)
        self.wk_vao = self.ctx.vertex_array(
            self.wk_prog,
            [
                (self.quad_vbo, "2f", "in_vert"),
                (self.wk_vbo, "4f 4f 1f/i", "in_rect", "in_color", "in_pressed"),
            ],
        )

        # Black Keys
        num_bk = len(self.layout.black_keys_static)
        self.bk_data = np.zeros((num_bk, 9), dtype=np.float32)
        self.bk_pitches = np.zeros(num_bk, dtype=np.int32)
        for i, (x, y, w, h, p) in enumerate(self.layout.black_keys_static):
            self.bk_data[i, 0:4] = [x, y, w, h]
            self.bk_pitches[i] = p

        self.bk_vbo = self.ctx.buffer(reserve=self.bk_data.nbytes, dynamic=True)
        self.bk_vao = self.ctx.vertex_array(
            self.bk_prog,
            [
                (self.quad_vbo, "2f", "in_vert"),
                (self.bk_vbo, "4f 4f 1f/i", "in_rect", "in_color", "in_pressed"),
            ],
        )

    def render_frame(self, t: float, dt: float) -> bytes:
        """Renders one video frame at time `t` (seconds) and returns RGBA bytes."""
        y_hit = self.layout.y_hit_ndc
        roll_height = 1.0 - y_hit
        inv_fall_dur = 1.0 / self.fall_duration

        # Reset active keys map
        active_pitches: Dict[int, Tuple[float, float, float, float]] = {}

        n_visible = 0

        if self.midi is not None and len(self.midi.notes) > 0:
            starts = self.midi.starts_np
            ends = self.midi.ends_np

            # Visible window: notes starting before t + fall_duration and ending after t
            t_max = t + self.fall_duration
            i_max = bisect.bisect_right(starts, t_max)

            # Advance search pointer
            while self.last_search_idx < i_max and ends[self.last_search_idx] < t:
                self.last_search_idx += 1

            i_min = self.last_search_idx
            if i_min < i_max:
                sub_ends = ends[i_min:i_max]
                vis_rel = np.flatnonzero(sub_ends >= t)
                if len(vis_rel) > 0:
                    vis_indices = i_min + vis_rel
                    n_vis = len(vis_indices)

                    v_starts = starts[vis_indices]
                    v_ends = ends[vis_indices]
                    v_pitches = self.midi.pitches_np[vis_indices]
                    v_colors = self.midi.colors_np[vis_indices]

                    # Filter to pitches inside active piano keyboard range
                    in_range = self.layout.pitch_in_range[v_pitches]
                    if np.any(in_range):
                        v_starts = v_starts[in_range]
                        v_ends = v_ends[in_range]
                        v_pitches = v_pitches[in_range]
                        v_colors = v_colors[in_range]
                        n_vis = len(v_pitches)

                        # Y coordinate calculations
                        # f_start: fraction from hit line to top
                        f_start = (v_starts - t) * inv_fall_dur
                        f_end = (v_ends - t) * inv_fall_dur

                        y_bot = np.maximum(y_hit, y_hit + f_start * roll_height)
                        y_top = np.minimum(1.0, y_hit + f_end * roll_height)
                        h_note = y_top - y_bot

                        # Filter valid positive height notes
                        valid_h = h_note > 0.001
                        if np.any(valid_h):
                            v_starts = v_starts[valid_h]
                            v_ends = v_ends[valid_h]
                            v_pitches = v_pitches[valid_h]
                            v_colors = v_colors[valid_h]
                            y_bot = y_bot[valid_h]
                            h_note = h_note[valid_h]
                            n_vis = len(v_pitches)

                            if n_vis > self.max_notes_instances:
                                # Resize buffer if needed
                                self.max_notes_instances = n_vis + 1000
                                self.notes_buffer = np.zeros((self.max_notes_instances, 12), dtype=np.float32)
                                self.notes_vbo = self.ctx.buffer(reserve=self.notes_buffer.nbytes, dynamic=True)
                                self.notes_vao = self.ctx.vertex_array(
                                    self.notes_prog,
                                    [
                                        (self.quad_vbo, "2f", "in_vert"),
                                        (self.notes_vbo, "4f 4f 4f/i", "in_rect", "in_color", "in_params"),
                                    ],
                                )

                            x_note = self.layout.pitch_to_note_x[v_pitches]
                            w_note = self.layout.pitch_to_note_w[v_pitches]

                            is_striking = (v_starts <= t) & (v_ends >= t)

                            # Fill notes buffer: [x, y, w, h, r, g, b, a, radius, is_active, 0, 0]
                            self.notes_buffer[:n_vis, 0] = x_note
                            self.notes_buffer[:n_vis, 1] = y_bot
                            self.notes_buffer[:n_vis, 2] = w_note
                            self.notes_buffer[:n_vis, 3] = h_note
                            self.notes_buffer[:n_vis, 4:8] = v_colors
                            self.notes_buffer[:n_vis, 8] = 6.0  # corner radius in px
                            self.notes_buffer[:n_vis, 9] = np.where(is_striking, 1.0, 0.0)
                            self.notes_buffer[:n_vis, 10:12] = 0.0

                            n_visible = n_vis

                            # Track active pressed pitches
                            active_idx = np.flatnonzero(is_striking)
                            for aidx in active_idx:
                                p = int(v_pitches[aidx])
                                c = v_colors[aidx]
                                active_pitches[p] = (c[0], c[1], c[2], c[3])

        # Update White Keys
        self.wk_data[:, 4:8] = 0.0
        self.wk_data[:, 8] = 0.0
        for i, p in enumerate(self.wk_pitches):
            if p in active_pitches:
                c = active_pitches[p]
                self.wk_data[i, 4:8] = c
                self.wk_data[i, 8] = 1.0

        self.wk_vbo.write(self.wk_data.tobytes())

        # Update Black Keys
        self.bk_data[:, 4:8] = 0.0
        self.bk_data[:, 8] = 0.0
        for i, p in enumerate(self.bk_pitches):
            if p in active_pitches:
                c = active_pitches[p]
                self.bk_data[i, 4:8] = c
                self.bk_data[i, 8] = 1.0

        self.bk_vbo.write(self.bk_data.tobytes())

        # Update Flares & Particles for active keys
        n_flares = 0
        if active_pitches:
            for p, col in active_pitches.items():
                if n_flares < self.max_flares:
                    cx = self.layout.pitch_to_center_x[p]
                    cy = y_hit + 0.015
                    w = self.layout.pitch_to_note_w[p] * 3.6
                    h = 0.09
                    self.flare_buffer[n_flares, 0:4] = [cx, cy, w, h]
                    self.flare_buffer[n_flares, 4:8] = col
                    n_flares += 1

                if self.enable_particles:
                    cx = self.layout.pitch_to_center_x[p]
                    self.particles.spawn(cx, y_hit, col[0], col[1], col[2], count=2)

        if n_flares > 0:
            self.flare_vbo.write(self.flare_buffer[:n_flares].tobytes())

        sparks_data = self.particles.update(dt) if self.enable_particles else np.empty(0)
        n_sparks = len(sparks_data)
        if n_sparks > 0:
            self.spark_vbo.write(sparks_data.tobytes())

        # --- RENDER PASSES ---
        self.ctx.clear(0.0, 0.0, 0.0, 1.0)

        # 1. Background
        self.ctx.disable(moderngl.BLEND)
        self.bg_vao.render(moderngl.TRIANGLE_STRIP)

        # 2. Falling Notes
        if n_visible > 0:
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
            self.notes_vbo.write(self.notes_buffer[:n_visible].tobytes())
            self.notes_vao.render(moderngl.TRIANGLE_STRIP, instances=n_visible)

        # 3. Hitbar Separator
        self.ctx.disable(moderngl.BLEND)
        self.hitbar_vao.render(moderngl.TRIANGLE_STRIP)

        # 4. Additive Bloom Flares
        if self.enable_glow and n_flares > 0:
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE
            self.flare_vao.render(moderngl.TRIANGLE_STRIP, instances=n_flares)

        # 5. Additive Sparks / Embers
        if self.enable_particles and n_sparks > 0:
            self.ctx.enable(moderngl.BLEND)
            self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE
            self.spark_vao.render(moderngl.TRIANGLE_STRIP, instances=n_sparks)

        # 6. Piano Keyboard: White Keys then Black Keys
        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self.wk_vao.render(moderngl.TRIANGLE_STRIP, instances=len(self.wk_data))
        self.bk_vao.render(moderngl.TRIANGLE_STRIP, instances=len(self.bk_data))

        # 7. Title & Instrument Badge Overlay
        t_video = t + self.lead_in
        self.title_overlay.render(t_video)

        # Read Framebuffer (RGBA 4-components for maximum DMA speed)
        return self.fbo.read(components=4)

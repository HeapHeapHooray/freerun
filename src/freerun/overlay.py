"""On-screen title and instrument badge overlay for Freerun."""

from __future__ import annotations
import json
import os
import subprocess
from typing import Optional, Tuple
import mido
import moderngl
import numpy as np
from PIL import Image, ImageDraw, ImageFont


def _load_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    """Finds and loads the cleanest sans-serif TrueType font available."""
    candidates = [
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/ubuntu/UbuntuSans[wdth,wght].ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return ImageFont.load_default()


def auto_detect_metadata(
    audio_path: Optional[str] = None,
    midi_path: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Detects song title, artist, and instruments from audio ID3 tags and MIDI tracks."""
    title: Optional[str] = None
    artist: Optional[str] = None
    instruments: Optional[str] = None

    # 1. Check audio ID3 tags via ffprobe
    if audio_path and os.path.exists(audio_path):
        try:
            res = subprocess.run(
                [
                    "ffprobe",
                    "-v", "quiet",
                    "-print_format", "json",
                    "-show_entries", "format_tags=title,artist,album",
                    audio_path,
                ],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if res.returncode == 0 and res.stdout:
                data = json.loads(res.stdout)
                tags = data.get("format", {}).get("tags", {})
                title = tags.get("title") or tags.get("TITLE")
                artist = tags.get("artist") or tags.get("ARTIST")
        except Exception:
            pass

    # 2. Check MIDI track names for instruments
    if midi_path and os.path.exists(midi_path):
        try:
            mid = mido.MidiFile(midi_path)
            track_names = []
            for track in mid.tracks:
                if track.name and track.name.strip():
                    name = track.name.strip()
                    if name.lower() not in ["tempo", "master", "meta", "time"]:
                        track_names.append(name)

            if track_names:
                instruments = ", ".join(track_names[:3])
        except Exception:
            pass

    return title, artist, instruments


OVERLAY_VS = """
#version 330 core
in vec2 in_vert; // (0,0) to (1,1)
uniform vec4 u_rect; // x, y, w, h in NDC
out vec2 uv;
void main() {
    uv = in_vert;
    vec2 pos = u_rect.xy + in_vert * u_rect.zw;
    gl_Position = vec4(pos, 0.0, 1.0);
}
"""

OVERLAY_FS = """
#version 330 core
in vec2 uv;
out vec4 fragColor;
uniform sampler2D u_texture;
uniform float u_alpha;

void main() {
    vec4 col = texture(u_texture, uv);
    fragColor = vec4(col.rgb, col.a * u_alpha);
}
"""


class TitleCardOverlay:
    """Renders a sleek glass badge showing the music title and instruments used."""

    def __init__(
        self,
        ctx: moderngl.Context,
        screen_width: int,
        screen_height: int,
        title: Optional[str] = None,
        instruments: Optional[str] = None,
        artist: Optional[str] = None,
        accent_color: Tuple[float, float, float] = (0.0, 0.95, 1.0),
        duration: float = 7.0,  # <= 0 means persistent
        fade_in: float = 1.0,
        fade_out: float = 1.5,
        position: str = "top-left",
    ):
        self.ctx = ctx
        self.screen_width = screen_width
        self.screen_height = screen_height
        self.title = title.strip() if title else None
        self.instruments = instruments.strip() if instruments else None
        self.artist = artist.strip() if artist else None
        self.accent_color = accent_color
        self.duration = duration
        self.fade_in = fade_in
        self.fade_out = fade_out
        self.position = position

        self.enabled = bool(self.title or self.instruments or self.artist)
        if not self.enabled:
            return

        # Generate texture
        self._init_texture()
        self._init_gl()

    def _init_texture(self):
        scale = self.screen_height / 1080.0
        pad_x = int(24 * scale)
        pad_y = int(20 * scale)

        title_size = max(18, int(34 * scale))
        sub_size = max(12, int(20 * scale))

        font_title = _load_font(title_size, bold=True)
        font_sub = _load_font(sub_size, bold=False)

        dummy_img = Image.new("RGBA", (1, 1))
        d = ImageDraw.Draw(dummy_img)

        lines = []
        if self.title:
            lines.append(("title", self.title, font_title))
        if self.artist:
            lines.append(("artist", f"by {self.artist}", font_sub))
        if self.instruments:
            lines.append(("instrument", f"Instrument: {self.instruments}", font_sub))

        w_content = 0
        h_content = pad_y * 2
        for kind, text, font in lines:
            bbox = d.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            w_content = max(w_content, tw)
            h_content += th + int(10 * scale)

        card_w = max(int(360 * scale), w_content + pad_x * 2 + int(16 * scale))
        card_h = max(int(80 * scale), h_content + int(6 * scale))

        img = Image.new("RGBA", (card_w, card_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Translucent dark glass rounded card
        radius = max(6, int(14 * scale))
        draw.rounded_rectangle(
            [(0, 0), (card_w, card_h)],
            radius=radius,
            fill=(12, 14, 20, 210),
            outline=(52, 58, 76, 185),
            width=1,
        )

        # Glowing accent stripe on left
        acc_r = int(min(255, max(0, self.accent_color[0] * 255)))
        acc_g = int(min(255, max(0, self.accent_color[1] * 255)))
        acc_b = int(min(255, max(0, self.accent_color[2] * 255)))

        stripe_w = max(3, int(5 * scale))
        draw.rounded_rectangle(
            [(int(4 * scale), int(8 * scale)), (int(4 * scale) + stripe_w, card_h - int(8 * scale))],
            radius=max(2, int(3 * scale)),
            fill=(acc_r, acc_g, acc_b, 240),
        )

        # Text rendering
        cur_y = pad_y
        text_x = pad_x + int(8 * scale)
        for kind, text, font in lines:
            bbox = font.getbbox(text)
            line_h = bbox[3] - bbox[1]
            if kind == "title":
                # Shadow + Title text
                draw.text((text_x + 1, cur_y + 1), text, font=font, fill=(0, 0, 0, 160))
                draw.text((text_x, cur_y), text, font=font, fill=(255, 255, 255, 255))
                cur_y += line_h + int(10 * scale)
            elif kind == "artist":
                draw.text((text_x, cur_y), text, font=font, fill=(185, 192, 210, 230))
                cur_y += line_h + int(8 * scale)
            elif kind == "instrument":
                draw.text((text_x, cur_y), text, font=font, fill=(acc_r, acc_g, acc_b, 240))
                cur_y += line_h + int(8 * scale)

        # OpenGL texture expects bottom-up row order
        img_flipped = img.transpose(Image.FLIP_TOP_BOTTOM)
        self.tex_w, self.tex_h = img.size
        self.texture = self.ctx.texture((self.tex_w, self.tex_h), 4, img_flipped.tobytes())
        self.texture.filter = (moderngl.LINEAR, moderngl.LINEAR)

    def _init_gl(self):
        self.prog = self.ctx.program(vertex_shader=OVERLAY_VS, fragment_shader=OVERLAY_FS)
        quad = np.array([
            0.0, 0.0,
            1.0, 0.0,
            0.0, 1.0,
            1.0, 1.0,
        ], dtype="f4")
        self.quad_vbo = self.ctx.buffer(quad.tobytes())
        self.vao = self.ctx.vertex_array(self.prog, [(self.quad_vbo, "2f", "in_vert")])

        # NDC bounds
        w_ndc = 2.0 * (self.tex_w / self.screen_width)
        h_ndc = 2.0 * (self.tex_h / self.screen_height)
        margin_x_ndc = 2.0 * (48.0 / self.screen_width)
        margin_y_ndc = 2.0 * (48.0 / self.screen_height)

        if self.position == "top-center":
            x_ndc = -w_ndc * 0.5
        elif self.position == "top-right":
            x_ndc = 1.0 - margin_x_ndc - w_ndc
        else:  # top-left
            x_ndc = -1.0 + margin_x_ndc

        y_ndc = 1.0 - margin_y_ndc - h_ndc
        self.prog["u_rect"].value = (x_ndc, y_ndc, w_ndc, h_ndc)
        self.prog["u_texture"].value = 0

    def get_alpha(self, t_video: float) -> float:
        """Returns the current opacity alpha at video time t (seconds from 0)."""
        if not self.enabled:
            return 0.0

        if t_video < 0:
            return 0.0

        # Persistent mode
        if self.duration <= 0:
            if t_video < self.fade_in:
                return max(0.0, min(1.0, t_video / self.fade_in))
            return 1.0

        # Fading title mode
        if t_video < self.fade_in:
            return max(0.0, min(1.0, t_video / self.fade_in))
        elif t_video <= self.duration:
            return 1.0
        elif t_video <= self.duration + self.fade_out:
            fade_progress = (t_video - self.duration) / self.fade_out
            return max(0.0, min(1.0, 1.0 - fade_progress))
        return 0.0

    def render(self, t_video: float):
        """Renders the title card badge if visible."""
        if not self.enabled:
            return

        alpha = self.get_alpha(t_video)
        if alpha <= 0.001:
            return

        self.ctx.enable(moderngl.BLEND)
        self.ctx.blend_func = moderngl.SRC_ALPHA, moderngl.ONE_MINUS_SRC_ALPHA
        self.prog["u_alpha"].value = alpha
        self.texture.use(location=0)
        self.vao.render(moderngl.TRIANGLE_STRIP)

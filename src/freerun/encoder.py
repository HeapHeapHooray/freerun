"""FFmpeg hardware-accelerated video encoding and audio muxing pipeline."""

from __future__ import annotations
import os
import queue
import shutil
import subprocess
import threading
from typing import Optional


def get_audio_duration(audio_path: str) -> Optional[float]:
    """Retrieves audio duration in seconds using ffprobe."""
    if not shutil.which("ffprobe"):
        return None
    try:
        res = subprocess.run(
            [
                "ffprobe",
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                audio_path,
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if res.returncode == 0 and res.stdout.strip():
            return float(res.stdout.strip())
    except Exception:
        pass
    return None


def detect_best_encoder() -> str:
    """Detects available hardware video encoders (VAAPI, NVENC, QSV) or defaults to libx264."""
    # 1. Test VAAPI on AMD / Intel Linux GPUs
    if os.path.exists("/dev/dri/renderD128"):
        try:
            res = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-init_hw_device", "vaapi=va:/dev/dri/renderD128",
                    "-f", "lavfi", "-i", "testsrc=duration=0.1:size=256x256:rate=30",
                    "-vf", "format=nv12,hwupload",
                    "-c:v", "h264_vaapi",
                    "-f", "null", "-",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
            )
            if res.returncode == 0:
                return "vaapi"
        except Exception:
            pass

    # 2. Test NVIDIA NVENC
    try:
        res = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=0.1:size=256x256:rate=30",
                "-c:v", "h264_nvenc",
                "-f", "null", "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        if res.returncode == 0:
            return "nvenc"
    except Exception:
        pass

    # 3. Test Intel Quick Sync
    try:
        res = subprocess.run(
            [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "testsrc=duration=0.1:size=256x256:rate=30",
                "-c:v", "h264_qsv",
                "-f", "null", "-",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
        if res.returncode == 0:
            return "qsv"
    except Exception:
        pass

    return "libx264"


class VideoEncoder:
    """High-throughput threaded pipe to FFmpeg for simultaneous encoding and audio muxing."""

    def __init__(
        self,
        output_path: str,
        width: int,
        height: int,
        fps: int = 60,
        audio_path: Optional[str] = None,
        audio_delay: float = 0.0,  # seconds (lead-in delay)
        encoder: str = "auto",
        crf: int = 18,
        preset: str = "ultrafast",
        queue_size: int = 16,
    ):
        self.output_path = output_path
        self.width = width
        self.height = height
        self.fps = fps
        self.audio_path = audio_path
        self.audio_delay = audio_delay
        self.queue_size = queue_size

        if encoder == "auto":
            self.encoder = detect_best_encoder()
        else:
            self.encoder = encoder.lower()

        self._build_and_start_process(crf, preset)

        # Asynchronous pipe queue and background writer thread
        self.queue: queue.Queue[Optional[bytes]] = queue.Queue(maxsize=self.queue_size)
        self.writer_thread = threading.Thread(target=self._pipe_writer, daemon=True)
        self.writer_thread.start()
        self.is_closed = False

    def _build_and_start_process(self, crf: int, preset: str):
        cmd = ["ffmpeg", "-y", "-loglevel", "error"]

        # Hardware initialization if using VAAPI
        if self.encoder == "vaapi":
            cmd.extend(["-init_hw_device", "vaapi=va:/dev/dri/renderD128"])

        # Video input stream (raw RGBA from pipe)
        cmd.extend([
            "-f", "rawvideo",
            "-pix_fmt", "rgba",
            "-s", f"{self.width}x{self.height}",
            "-r", str(self.fps),
            "-i", "-",
        ])

        # Audio input stream if audio is provided
        has_audio = self.audio_path is not None and os.path.exists(self.audio_path)
        if has_audio:
            cmd.extend(["-i", self.audio_path])

        # Filter graphs
        v_filter = None
        a_filter = None

        if self.encoder == "vaapi":
            v_filter = "vflip,format=nv12,hwupload"
        else:
            v_filter = "vflip"

        if has_audio and abs(self.audio_delay) > 0.001:
            if self.audio_delay > 0:
                delay_ms = int(round(self.audio_delay * 1000.0))
                a_filter = f"adelay={delay_ms}|{delay_ms}"
            else:
                trim_sec = abs(self.audio_delay)
                a_filter = f"atrim=start={trim_sec:.4f},asetpts=PTS-STARTPTS"

        # Combine filters into -filter_complex
        filter_parts = []
        v_out_label = "0:v"
        a_out_label = "1:a"

        if v_filter:
            filter_parts.append(f"[0:v]{v_filter}[vout]")
            v_out_label = "[vout]"

        if a_filter:
            filter_parts.append(f"[1:a]{a_filter}[aout]")
            a_out_label = "[aout]"

        if filter_parts:
            cmd.extend(["-filter_complex", ";".join(filter_parts)])

        # Stream mapping
        cmd.extend(["-map", v_out_label])
        if has_audio:
            cmd.extend(["-map", a_out_label])

        # Video codec options
        if self.encoder == "vaapi":
            cmd.extend(["-c:v", "h264_vaapi", "-qp", str(crf + 2)])
        elif self.encoder == "nvenc":
            cmd.extend(["-c:v", "h264_nvenc", "-preset", "p1", "-cq", str(crf), "-pix_fmt", "yuv420p"])
        elif self.encoder == "qsv":
            cmd.extend(["-c:v", "h264_qsv", "-global_quality", str(crf), "-pix_fmt", "nv12"])
        else:  # libx264
            cmd.extend([
                "-c:v", "libx264",
                "-preset", preset,
                "-tune", "fastdecode",
                "-crf", str(crf),
                "-pix_fmt", "yuv420p",
            ])

        # Audio codec options
        if has_audio:
            cmd.extend(["-c:a", "aac", "-b:a", "192k"])

        # Finish flags
        cmd.extend(["-shortest", self.output_path])

        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )

        self.stderr_lines: list[str] = []

        def _stderr_reader():
            try:
                if self.proc.stderr:
                    for line in iter(self.proc.stderr.readline, b""):
                        self.stderr_lines.append(line.decode("utf-8", errors="ignore"))
                        if len(self.stderr_lines) > 200:
                            self.stderr_lines.pop(0)
            except Exception:
                pass

        self.stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
        self.stderr_thread.start()

    def _pipe_writer(self):
        try:
            while True:
                chunk = self.queue.get()
                if chunk is None:
                    break
                if self.proc.stdin is not None:
                    self.proc.stdin.write(chunk)
                self.queue.task_done()
        except (BrokenPipeError, OSError):
            pass

    def write_frame(self, frame_bytes: bytes):
        """Pushes a frame to the async encoder queue."""
        if self.proc.poll() is not None:
            err = "".join(self.stderr_lines).strip()
            raise RuntimeError(f"FFmpeg process exited prematurely (code {self.proc.returncode}): {err}")
        self.queue.put(frame_bytes)

    def close(self):
        """Signals end of stream, waits for writing to complete and FFmpeg to finalize."""
        if self.is_closed:
            return
        self.is_closed = True

        self.queue.put(None)
        self.writer_thread.join()

        if self.proc.stdin:
            try:
                self.proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        self.proc.wait()
        self.stderr_thread.join(timeout=2.0)
        if self.proc.returncode != 0:
            err_msg = "".join(self.stderr_lines).strip() or "Unknown FFmpeg error"
            raise RuntimeError(f"FFmpeg encoding failed (code {self.proc.returncode}): {err_msg}")

import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2


def format_timecode(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    whole_secs = int(secs)
    millis = int(round((secs - whole_secs) * 1000))
    if millis >= 1000:
        whole_secs += 1
        millis -= 1000
    if whole_secs >= 60:
        minutes += 1
        whole_secs -= 60
    if minutes >= 60:
        hours += 1
        minutes -= 60
    return f"{hours:02d}-{minutes:02d}-{whole_secs:02d}.{millis:03d}"


def ffmpeg_executable() -> Optional[str]:
    path = shutil.which("ffmpeg")
    if path:
        return path

    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


class VideoSegmentExporter:
    """Export detected shots as individual video files."""

    def __init__(self, video_path: str, output_dir: str):
        self.video_path = video_path
        self.output_dir = Path(output_dir)
        self.video_dir = self.output_dir / "videos"
        self.video_dir.mkdir(parents=True, exist_ok=True)

    def export_segments(
        self,
        shots: List[Tuple[int, int]],
        mode: str = "precise",
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> Dict:
        fps, width, height, total_frames = self._probe_video()
        ffmpeg_path = ffmpeg_executable()
        requested_mode = mode
        actual_mode = mode if ffmpeg_path else "opencv_reencode"
        exports = []

        for index, (start, end) in enumerate(shots):
            filename = self._segment_filename(index, start, end, fps)
            output_path = self.video_dir / filename

            if ffmpeg_path:
                if mode == "copy":
                    method = "ffmpeg_copy"
                    self._export_with_ffmpeg_copy(ffmpeg_path, start, end, fps, output_path)
                else:
                    method = "ffmpeg_reencode"
                    self._export_with_ffmpeg_reencode(ffmpeg_path, start, end, fps, output_path)
            else:
                method = "opencv_reencode"
                self._export_with_opencv(start, end, fps, width, height, output_path)

            exports.append(
                {
                    "index": index + 1,
                    "start_frame": start,
                    "end_frame": end,
                    "start_time": start / fps if fps > 0 else 0,
                    "end_time": end / fps if fps > 0 else 0,
                    "duration": (end - start + 1) / fps if fps > 0 else 0,
                    "filename": filename,
                    "filepath": str(output_path),
                    "method": method,
                }
            )

            if progress_callback and shots:
                progress_callback(int(((index + 1) / len(shots)) * 100))

        metadata = {
            "export_type": "shot_videos",
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "video_path": self.video_path,
            "fps": fps,
            "width": width,
            "height": height,
            "total_frames": total_frames,
            "total_shots": len(shots),
            "requested_mode": requested_mode,
            "actual_mode": actual_mode,
            "ffmpeg_available": bool(ffmpeg_path),
            "outputs": exports,
        }
        self._save_metadata(metadata)
        return metadata

    def _probe_video(self) -> Tuple[float, int, int, int]:
        cap = cv2.VideoCapture(self.video_path)
        if not cap.isOpened():
            raise ValueError(f"Unable to open video: {self.video_path}")
        try:
            fps = float(cap.get(cv2.CAP_PROP_FPS) or 0)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        finally:
            cap.release()

        if fps <= 0:
            raise ValueError("Unable to read video fps.")
        return fps, width, height, total_frames

    def _segment_filename(self, index: int, start: int, end: int, fps: float) -> str:
        start_tc = format_timecode(start / fps if fps > 0 else 0)
        end_tc = format_timecode(end / fps if fps > 0 else 0)
        return (
            f"shot_{index + 1:03d}_"
            f"f{start:06d}-{end:06d}_"
            f"t{start_tc}-{end_tc}.mp4"
        )

    def _export_with_ffmpeg_copy(
        self,
        ffmpeg_path: str,
        start: int,
        end: int,
        fps: float,
        output_path: Path,
    ):
        start_time = start / fps
        duration = (end - start + 1) / fps
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{start_time:.6f}",
            "-t",
            f"{duration:.6f}",
            "-i",
            self.video_path,
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c",
            "copy",
            "-avoid_negative_ts",
            "make_zero",
            "-reset_timestamps",
            "1",
            str(output_path),
        ]
        self._run_command(command)

    def _export_with_ffmpeg_reencode(
        self,
        ffmpeg_path: str,
        start: int,
        end: int,
        fps: float,
        output_path: Path,
    ):
        start_time = start / fps
        duration = (end - start + 1) / fps
        command = [
            ffmpeg_path,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            self.video_path,
            "-ss",
            f"{start_time:.6f}",
            "-t",
            f"{duration:.6f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-crf",
            "16",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        self._run_command(command)

    def _run_command(self, command: List[str]):
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "ffmpeg export failed")

    def _export_with_opencv(
        self,
        start: int,
        end: int,
        fps: float,
        width: int,
        height: int,
        output_path: Path,
    ):
        suffix = output_path.suffix or ".mp4"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp_file:
            temp_path = temp_file.name

        cap = cv2.VideoCapture(self.video_path)
        writer = cv2.VideoWriter(
            temp_path,
            cv2.VideoWriter_fourcc(*"mp4v"),
            fps,
            (width, height),
        )
        try:
            if not cap.isOpened():
                raise ValueError(f"Unable to open video: {self.video_path}")
            if not writer.isOpened():
                raise ValueError(f"Unable to create video: {output_path}")

            cap.set(cv2.CAP_PROP_POS_FRAMES, start)
            for _frame_idx in range(start, end + 1):
                ok, frame = cap.read()
                if not ok:
                    break
                writer.write(frame)
        finally:
            cap.release()
            writer.release()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            output_path.unlink()
        shutil.move(temp_path, output_path)

    def _save_metadata(self, metadata: Dict):
        metadata_path = self.output_dir / "metadata.json"
        metadata_path.write_text(
            json.dumps(metadata, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

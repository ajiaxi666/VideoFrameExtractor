import json
import os
from pathlib import Path
from typing import Dict, List, Optional

import cv2


class ImageSaver:
    """Save extracted frames and metadata."""

    def __init__(self, output_dir: str, format: str = "png"):
        self.output_dir = output_dir
        self.format = format.lower()
        self.saved_frames = []

        Path(output_dir).mkdir(parents=True, exist_ok=True)

    def save_frame(
        self,
        frame,
        frame_idx: int,
        shot_idx: Optional[int] = None,
        keyframe_idx: Optional[int] = None,
    ) -> str:
        if shot_idx is not None and keyframe_idx is not None:
            filename = (
                f"shot_{shot_idx + 1:03d}_key_{keyframe_idx + 1:02d}_"
                f"frame_{frame_idx:06d}.{self.format}"
            )
        elif shot_idx is not None:
            filename = f"shot_{shot_idx + 1:03d}_frame_{frame_idx:06d}.{self.format}"
        else:
            filename = f"frame_{frame_idx:06d}.{self.format}"

        filepath = os.path.join(self.output_dir, filename)

        if self.format == "png":
            encode_params = [cv2.IMWRITE_PNG_COMPRESSION, 0]
        else:
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, 95]

        encoded, buffer = cv2.imencode(f".{self.format}", frame, encode_params)
        if not encoded:
            raise IOError(f"无法编码图片: {filepath}")

        # cv2.imwrite can fail on Windows paths containing Chinese characters.
        # Python's file APIs handle Unicode paths reliably.
        with open(filepath, "wb") as f:
            f.write(buffer.tobytes())

        self.saved_frames.append(
            {
                "filename": filename,
                "frame_idx": frame_idx,
                "shot_idx": shot_idx,
                "keyframe_idx": keyframe_idx,
                "filepath": filepath,
            }
        )

        return filepath

    def save_frames_batch(
        self,
        frames: List,
        frame_indices: List[int],
        shot_indices: Optional[List[int]] = None,
    ) -> List[str]:
        filepaths = []

        for i, (frame, frame_idx) in enumerate(zip(frames, frame_indices)):
            shot_idx = shot_indices[i] if shot_indices else None
            filepath = self.save_frame(frame, frame_idx, shot_idx)
            filepaths.append(filepath)

        return filepaths

    def save_metadata(self, metadata: Dict):
        metadata_path = os.path.join(self.output_dir, "metadata.json")

        data = {
            "format": self.format,
            "total_frames": len(self.saved_frames),
            "frames": self.saved_frames,
            "metadata": metadata,
        }

        with open(metadata_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

        return metadata_path

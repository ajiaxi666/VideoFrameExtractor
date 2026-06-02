import hashlib
import json
from pathlib import Path
from typing import Dict, Optional

import numpy as np

FEATURE_CACHE_VERSION = 2


def video_sample_hash(path: Path, size: Optional[int] = None) -> str:
    size = path.stat().st_size if size is None else int(size)
    digest = hashlib.sha1()
    digest.update(str(size).encode("utf-8"))
    chunk_size = 1024 * 1024
    offsets = [0]
    if size > chunk_size * 2:
        offsets.append(max(0, size // 2 - chunk_size // 2))
    if size > chunk_size:
        offsets.append(max(0, size - chunk_size))

    with path.open("rb") as f:
        for offset in sorted(set(offsets)):
            f.seek(offset)
            digest.update(f.read(chunk_size))
    return digest.hexdigest()


def video_signature(video_path: str) -> Dict:
    path = Path(video_path)
    stat = path.stat()
    return {
        "name": path.name,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sample_hash": video_sample_hash(path, stat.st_size),
    }


def cache_key(signature: Dict) -> str:
    key_payload = {
        "name": signature.get("name"),
        "size": signature.get("size"),
        "sample_hash": signature.get("sample_hash"),
    }
    raw = json.dumps(key_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha1(raw).hexdigest()


def feature_cache_path(
    cache_dir: str,
    video_path: str,
    analysis_width: int,
    analysis_frame_step: int = 1,
) -> Path:
    signature = video_signature(video_path)
    return Path(cache_dir) / (
        f"{cache_key(signature)}_w{int(analysis_width)}_s{int(analysis_frame_step)}.npz"
    )


class FeatureCache:
    """Stores per-frame low-cost detection features for a video."""

    def __init__(self, cache_dir: Optional[str]):
        self.cache_dir = Path(cache_dir) if cache_dir else None

    def load(
        self,
        video_path: str,
        total_frames: int,
        fps: float,
        analysis_width: int,
        analysis_frame_step: int = 1,
    ):
        if not self.cache_dir:
            return None
        path = feature_cache_path(
            str(self.cache_dir),
            video_path,
            analysis_width,
            analysis_frame_step,
        )
        if not path.exists():
            return None

        try:
            with np.load(path, allow_pickle=False) as data:
                metadata = json.loads(str(data["metadata"].item()))
                if not self._metadata_matches(
                    metadata,
                    video_path,
                    total_frames,
                    fps,
                    analysis_width,
                    analysis_frame_step,
                ):
                    return None
                return {
                    "metadata": metadata,
                    "content_scores": data["content_scores"].astype(np.float32, copy=False),
                    "histogram_scores": data["histogram_scores"].astype(np.float32, copy=False),
                    "sampled_frames": data["sampled_frames"].astype(np.int32, copy=False),
                    "sample_step": int(metadata.get("analysis_frame_step", analysis_frame_step)),
                    "path": str(path),
                }
        except Exception:
            return None

    def save(
        self,
        video_path: str,
        total_frames: int,
        fps: float,
        analysis_width: int,
        analysis_frame_step: int,
        content_scores,
        histogram_scores,
        sampled_frames,
    ):
        if not self.cache_dir:
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = feature_cache_path(
            str(self.cache_dir),
            video_path,
            analysis_width,
            analysis_frame_step,
        )
        metadata = {
            "version": FEATURE_CACHE_VERSION,
            "video_signature": video_signature(video_path),
            "total_frames": int(total_frames),
            "fps": float(fps),
            "analysis_width": int(analysis_width),
            "analysis_frame_step": int(analysis_frame_step),
        }
        np.savez_compressed(
            path,
            metadata=np.array(json.dumps(metadata, ensure_ascii=False)),
            content_scores=np.asarray(content_scores, dtype=np.float32),
            histogram_scores=np.asarray(histogram_scores, dtype=np.float32),
            sampled_frames=np.asarray(sampled_frames, dtype=np.int32),
        )
        return path

    def _metadata_matches(
        self,
        metadata: Dict,
        video_path: str,
        total_frames: int,
        fps: float,
        analysis_width: int,
        analysis_frame_step: int,
    ) -> bool:
        if int(metadata.get("version", -1)) != FEATURE_CACHE_VERSION:
            return False
        if int(metadata.get("analysis_width", -1)) != int(analysis_width):
            return False
        if int(metadata.get("analysis_frame_step", -1)) != int(analysis_frame_step):
            return False
        if int(metadata.get("total_frames", -1)) != int(total_frames):
            return False
        if abs(float(metadata.get("fps", 0.0)) - float(fps)) > 0.01:
            return False

        expected = metadata.get("video_signature") or {}
        current = video_signature(video_path)
        return (
            int(expected.get("size", -1)) == int(current.get("size", -2))
            and expected.get("sample_hash") == current.get("sample_hash")
        )

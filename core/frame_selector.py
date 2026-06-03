from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np


@dataclass
class FrameSelectionSettings:
    frames_per_shot: int = 2
    max_samples_per_shot: int = 90
    edge_margin_ratio: float = 0.08
    min_keyframe_gap_ratio: float = 0.18


class FrameSelector:
    """Select representative, high-quality keyframes from each shot."""

    def __init__(
        self,
        frames_per_shot: int = 2,
        max_samples_per_shot: int = 90,
        edge_margin_ratio: float = 0.08,
        min_keyframe_gap_ratio: float = 0.18,
    ):
        self.settings = FrameSelectionSettings(
            frames_per_shot=max(1, int(frames_per_shot)),
            max_samples_per_shot=max(1, int(max_samples_per_shot)),
            edge_margin_ratio=max(0.0, min(float(edge_margin_ratio), 0.45)),
            min_keyframe_gap_ratio=max(0.0, min(float(min_keyframe_gap_ratio), 0.5)),
        )
        self.frame_scores = {}

    def select_best_frames(
        self,
        video_path: str,
        shots: List[Tuple[int, int]],
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> List[List[int]]:
        """Return a list of selected frame indices for every shot."""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"无法打开视频文件: {video_path}")

        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        sample_sets = [self._sample_indices(start, end) for start, end in shots]
        sample_lookup = {
            int(frame_idx): shot_idx
            for shot_idx, sample_set in enumerate(sample_sets)
            for frame_idx in sample_set
        }
        wanted_frames = sorted(
            frame_idx
            for frame_idx in sample_lookup
            if 0 <= frame_idx < total_frames
        )
        candidates: List[List[Tuple[float, int]]] = [[] for _ in shots]
        frame_idx = -1
        last_reported = 50

        for target_frame in wanted_frames:
            exhausted = False
            while frame_idx < target_frame:
                if not cap.grab():
                    exhausted = True
                    break
                frame_idx += 1

                if progress_callback and total_frames > 0:
                    pct = 50 + int((frame_idx / total_frames) * 49)
                    if pct - last_reported >= 2:
                        progress_callback(pct)
                        last_reported = pct

            if exhausted or frame_idx != target_frame:
                break

            ret, frame = cap.retrieve()
            if ret:
                shot_idx = sample_lookup[target_frame]
                score = self._calculate_frame_score(frame)
                self.frame_scores[target_frame] = score
                candidates[shot_idx].append((score, target_frame))

        cap.release()

        selected_frames = [
            self._choose_spread_frames(shot_candidates, start, end)
            for shot_candidates, (start, end) in zip(candidates, shots)
        ]

        if progress_callback:
            progress_callback(99)

        return selected_frames

    def _sample_indices(self, start_frame: int, end_frame: int) -> set:
        shot_len = end_frame - start_frame + 1
        margin = max(0, int(shot_len * self.settings.edge_margin_ratio))
        inner_start = start_frame + margin
        inner_end = end_frame - margin
        if inner_end <= inner_start:
            inner_start, inner_end = start_frame, end_frame

        inner_len = inner_end - inner_start + 1
        if inner_len <= self.settings.max_samples_per_shot:
            return set(range(inner_start, inner_end + 1))

        step = inner_len / self.settings.max_samples_per_shot
        return {
            inner_start + int(i * step)
            for i in range(self.settings.max_samples_per_shot)
        }

    def _choose_spread_frames(
        self,
        candidates: List[Tuple[float, int]],
        start_frame: int,
        end_frame: int,
    ) -> List[int]:
        target = min(self.settings.frames_per_shot, end_frame - start_frame + 1)
        if target <= 0:
            return []

        if not candidates:
            midpoint = (start_frame + end_frame) // 2
            return [midpoint]

        ranked = sorted(candidates, key=lambda item: item[0], reverse=True)
        shot_len = end_frame - start_frame + 1
        min_gap = max(1, int(shot_len * self.settings.min_keyframe_gap_ratio))
        selected: List[int] = []

        while len(selected) < target and min_gap >= 1:
            for _score, frame_idx in ranked:
                if frame_idx in selected:
                    continue
                if all(abs(frame_idx - picked) >= min_gap for picked in selected):
                    selected.append(frame_idx)
                    if len(selected) >= target:
                        break
            if len(selected) >= target:
                break
            min_gap //= 2

        if len(selected) < target:
            for _score, frame_idx in ranked:
                if frame_idx not in selected:
                    selected.append(frame_idx)
                    if len(selected) >= target:
                        break

        return sorted(selected)

    def _calculate_frame_score(self, frame: np.ndarray) -> float:
        """Score a frame by sharpness, detail, contrast, exposure, and color."""
        height, width = frame.shape[:2]
        if width > 640:
            scale = 640 / width
            frame = cv2.resize(frame, (640, int(height * scale)), interpolation=cv2.INTER_AREA)

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

        sharpness = cv2.Laplacian(gray, cv2.CV_64F).var()
        sharpness_score = min(sharpness / 420.0, 1.0)

        edges = cv2.Canny(gray, 50, 150)
        edge_ratio = float(np.sum(edges > 0) / edges.size)
        info_score = min(edge_ratio * 9.0, 1.0)

        contrast = float(gray.std())
        contrast_score = min(contrast / 72.0, 1.0)

        brightness = float(gray.mean())
        brightness_score = max(0.0, 1.0 - abs(brightness - 128.0) / 128.0)

        saturation = float(hsv[:, :, 1].mean() / 255.0)
        saturation_score = min(saturation * 1.8, 1.0)

        return (
            sharpness_score * 0.42
            + info_score * 0.23
            + contrast_score * 0.18
            + brightness_score * 0.10
            + saturation_score * 0.07
        )

    def get_frame_at(self, video_path: str, frame_idx: int) -> np.ndarray:
        cap = cv2.VideoCapture(video_path)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        cap.release()
        if not ret:
            raise ValueError(f"无法读取第 {frame_idx} 帧")
        return frame

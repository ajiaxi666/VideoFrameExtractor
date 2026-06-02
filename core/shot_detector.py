from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple

import cv2
import numpy as np
from scenedetect import AdaptiveDetector, ContentDetector, FrameTimecode, detect

from core.feature_cache import FeatureCache


@dataclass
class ShotDetectionSettings:
    """Parameters used by the shot detector.

    Lower thresholds detect more cuts. The hybrid mode is intentionally more
    sensitive for short social/video-ad style edits where PySceneDetect alone
    can under-count shots.
    """

    mode: str = "hybrid"
    content_threshold: float = 12.0
    adaptive_threshold: float = 2.0
    histogram_threshold: float = 0.16
    min_scene_len_seconds: float = 0.35
    histogram_enabled: bool = True
    analysis_width: int = 240
    analysis_frame_step: int = 5
    merge_similar_shots: bool = True
    merge_similarity_threshold: float = 0.08
    merge_max_shot_seconds: float = 1.0
    guard_weak_motion_cuts: bool = True
    feature_cache_enabled: bool = True
    feature_cache_dir: Optional[str] = None


class ShotDetector:
    """Shot boundary detector based on PySceneDetect plus an OpenCV fallback."""

    def __init__(
        self,
        threshold: Optional[float] = None,
        use_adaptive: bool = False,
        mode: str = "hybrid",
        content_threshold: float = 12.0,
        adaptive_threshold: float = 2.0,
        histogram_threshold: float = 0.16,
        min_scene_len_seconds: float = 0.35,
        histogram_enabled: bool = True,
        analysis_width: int = 240,
        analysis_frame_step: int = 5,
        merge_similar_shots: bool = True,
        merge_similarity_threshold: float = 0.08,
        merge_max_shot_seconds: float = 1.0,
        guard_weak_motion_cuts: bool = True,
        feature_cache_enabled: bool = True,
        feature_cache_dir: Optional[str] = None,
    ):
        if threshold is not None:
            content_threshold = threshold
            if mode == "hybrid" and use_adaptive:
                mode = "adaptive"
            elif mode == "hybrid" and not use_adaptive:
                mode = "content"

        self.settings = ShotDetectionSettings(
            mode=mode,
            content_threshold=content_threshold,
            adaptive_threshold=adaptive_threshold,
            histogram_threshold=histogram_threshold,
            min_scene_len_seconds=min_scene_len_seconds,
            histogram_enabled=histogram_enabled,
            analysis_width=analysis_width,
            analysis_frame_step=max(1, int(analysis_frame_step)),
            merge_similar_shots=merge_similar_shots,
            merge_similarity_threshold=merge_similarity_threshold,
            merge_max_shot_seconds=merge_max_shot_seconds,
            guard_weak_motion_cuts=guard_weak_motion_cuts,
            feature_cache_enabled=feature_cache_enabled,
            feature_cache_dir=feature_cache_dir,
        )
        self.shot_boundaries: List[int] = []
        self.last_cut_candidates = {
            "content": [],
            "adaptive": [],
            "histogram": [],
        }
        self.similar_merge_count = 0
        self.used_feature_cache = False
        self.feature_cache_path = None

    def detect_shots(
        self,
        video_path: str,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> List[Tuple[int, int]]:
        """Detect shots and return [(start_frame, end_frame), ...]."""
        total_frames, fps = self._probe_video(video_path)
        if total_frames <= 0:
            return []

        min_scene_len = max(1, int(round(self.settings.min_scene_len_seconds * fps)))
        mode = self.settings.mode
        cuts: List[int] = []
        feature_cache = FeatureCache(self.settings.feature_cache_dir)
        features = None
        if progress_callback:
            progress_callback(5)

        if self.settings.feature_cache_enabled:
            features = feature_cache.load(
                video_path,
                total_frames,
                fps,
                self.settings.analysis_width,
                self.settings.analysis_frame_step,
            )

        if features is not None:
            self.used_feature_cache = True
            self.feature_cache_path = features.get("path")
            cuts = self._cuts_from_features(features, min_scene_len, mode)
            if progress_callback:
                progress_callback(48)
        else:
            self.used_feature_cache = False
            self.feature_cache_path = None
            cuts = self._detect_and_collect_features(
                video_path=video_path,
                total_frames=total_frames,
                fps=fps,
                min_scene_len=min_scene_len,
                mode=mode,
                progress_callback=progress_callback,
                feature_cache=feature_cache,
            )

        if progress_callback:
            progress_callback(48)

        merged_cuts = self._merge_cuts(cuts, total_frames, min_scene_len)
        shots = self._cuts_to_shots(merged_cuts, total_frames)
        if self.settings.merge_similar_shots:
            shots = self._merge_similar_shots(video_path, shots, fps)
        else:
            self.similar_merge_count = 0
        self.shot_boundaries = [shots[0][0]] + [start for start, _end in shots[1:]] + [shots[-1][1]]

        if progress_callback:
            progress_callback(50)

        return shots

    def _detect_and_collect_features(
        self,
        video_path: str,
        total_frames: int,
        fps: float,
        min_scene_len: int,
        mode: str,
        progress_callback: Optional[Callable[[int], None]],
        feature_cache: FeatureCache,
    ) -> List[int]:
        features = self._collect_sampled_features(
            video_path=video_path,
            total_frames=total_frames,
            fps=fps,
            progress_callback=progress_callback,
        )

        if self.settings.feature_cache_enabled:
            saved_path = feature_cache.save(
                video_path=video_path,
                total_frames=total_frames,
                fps=fps,
                analysis_width=self.settings.analysis_width,
                analysis_frame_step=self.settings.analysis_frame_step,
                content_scores=features["content_scores"],
                histogram_scores=features["histogram_scores"],
                sampled_frames=features["sampled_frames"],
            )
            self.feature_cache_path = str(saved_path) if saved_path else None

        cuts = self._cuts_from_features(features, min_scene_len, mode)
        return self._refine_sampled_cuts(
            video_path,
            cuts,
            total_frames,
            self.settings.analysis_frame_step,
        )

    def _cuts_from_features(self, features: dict, min_scene_len: int, mode: str) -> List[int]:
        cuts_by_source = {
            "content": [],
            "adaptive": [],
            "histogram": [],
        }
        content_scores = features["content_scores"]
        histogram_scores = features["histogram_scores"]
        sampled_frames = features.get("sampled_frames")
        content_threshold, adaptive_threshold, histogram_threshold = self._thresholds_for_features(
            features
        )

        if mode in {"content", "hybrid"}:
            content_cuts = self._detect_content_from_scores(
                content_scores,
                content_threshold,
                min_scene_len,
            )
            self.last_cut_candidates["content"] = content_cuts
            cuts_by_source["content"] = content_cuts

        if mode in {"adaptive", "hybrid"}:
            if sampled_frames is not None and len(sampled_frames) > 0:
                adaptive_cuts = self._detect_adaptive_from_sampled_scores(
                    content_scores,
                    sampled_frames,
                    adaptive_threshold,
                    min_scene_len,
                )
            else:
                adaptive_cuts = self._detect_adaptive_from_scores(
                    content_scores,
                    adaptive_threshold,
                    min_scene_len,
                )
            self.last_cut_candidates["adaptive"] = adaptive_cuts
            cuts_by_source["adaptive"] = adaptive_cuts

        if self.settings.histogram_enabled and mode in {"histogram", "hybrid"}:
            histogram_cuts = self._detect_histogram_from_scores(
                histogram_scores,
                min_scene_len,
                histogram_threshold,
            )
            self.last_cut_candidates["histogram"] = histogram_cuts
            cuts_by_source["histogram"] = histogram_cuts

        return self._filter_cut_candidates(
            cuts_by_source,
            content_scores,
            histogram_scores,
            mode,
            content_threshold,
            histogram_threshold,
        )

    def _filter_cut_candidates(
        self,
        cuts_by_source: dict,
        content_scores,
        histogram_scores,
        mode: str,
        content_threshold: float,
        histogram_threshold: float,
    ) -> List[int]:
        cuts = sorted(
            set(
                int(cut)
                for detector_cuts in cuts_by_source.values()
                for cut in detector_cuts
            )
        )
        if (
            mode != "hybrid"
            or not self.settings.guard_weak_motion_cuts
            or not cuts
        ):
            return cuts

        content_scores = np.asarray(content_scores, dtype=np.float32)
        histogram_scores = np.asarray(histogram_scores, dtype=np.float32)
        histogram_threshold = float(histogram_threshold)
        content_threshold = float(content_threshold)
        strong_content_threshold = content_threshold * 1.8
        histogram_support_threshold = histogram_threshold * 0.55
        tolerance = 2

        filtered: List[int] = []
        for cut in cuts:
            content_score = self._score_at(content_scores, cut)
            histogram_score = self._score_at(histogram_scores, cut)
            sources = {
                name
                for name, detector_cuts in cuts_by_source.items()
                if any(abs(int(source_cut) - cut) <= tolerance for source_cut in detector_cuts)
            }

            if "histogram" in sources:
                filtered.append(cut)
                continue
            if histogram_score >= histogram_support_threshold:
                filtered.append(cut)
                continue
            if content_score >= strong_content_threshold:
                filtered.append(cut)
                continue
            if len(sources) >= 2 and content_score >= content_threshold * 1.25:
                filtered.append(cut)

        return filtered

    def _thresholds_for_features(self, features: dict) -> Tuple[float, float, float]:
        sample_step = int(features.get("sample_step") or self.settings.analysis_frame_step)
        sampled_frames = features.get("sampled_frames")
        is_sampled = sampled_frames is not None and len(sampled_frames) > 0 and sample_step > 1
        threshold_scale = max(1.0, sample_step / 2.0) if is_sampled else 1.0
        adaptive_scale = 1.0 + (threshold_scale - 1.0) * 0.4
        return (
            float(self.settings.content_threshold) * threshold_scale,
            float(self.settings.adaptive_threshold) * adaptive_scale,
            float(self.settings.histogram_threshold) * threshold_scale,
        )

    def _score_at(self, scores, frame_idx: int) -> float:
        if frame_idx < 0 or frame_idx >= len(scores):
            return 0.0
        return float(scores[frame_idx])

    def _probe_video(self, video_path: str) -> Tuple[int, float]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"????????: {video_path}")
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        cap.release()
        return total_frames, fps

    def _detect_with_pyscenedetect(self, video_path: str, detector) -> List[int]:
        scene_list = detect(video_path, detector)
        cuts: List[int] = []
        for scene_index, (scene_start, _scene_end) in enumerate(scene_list):
            if scene_index == 0:
                continue
            cuts.append(scene_start.get_frames())
        return cuts

    def _collect_sampled_features(
        self,
        video_path: str,
        total_frames: int,
        fps: float,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> dict:
        """Collect low-resolution frame-difference features without retrieving every 4K frame."""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"????????: {video_path}")

        step = max(1, int(self.settings.analysis_frame_step))
        content_scores = np.zeros(total_frames, dtype=np.float32)
        histogram_scores = np.zeros(total_frames, dtype=np.float32)
        sampled_frames: List[int] = []
        previous_hist = None
        previous_gray = None
        previous_content_channels = None
        frame_idx = -1
        last_reported = 5

        try:
            while True:
                if not cap.grab():
                    break
                frame_idx += 1
                should_sample = frame_idx % step == 0 or frame_idx >= total_frames - 1

                if should_sample:
                    ret, frame = cap.retrieve()
                    if not ret:
                        break
                    analysis_frame = self._resize_for_analysis(frame)
                    content_score, previous_content_channels = self._content_score_for_frame(
                        analysis_frame,
                        previous_content_channels,
                        already_resized=True,
                    )
                    hist_score, previous_hist, previous_gray = self._histogram_score_for_frame(
                        analysis_frame,
                        previous_hist,
                        previous_gray,
                        already_resized=True,
                    )
                    content_scores[frame_idx] = content_score
                    histogram_scores[frame_idx] = hist_score
                    sampled_frames.append(frame_idx)

                if progress_callback and total_frames > 0:
                    pct = 5 + int((frame_idx / total_frames) * 30)
                    if pct > last_reported:
                        progress_callback(min(pct, 35))
                        last_reported = pct
        finally:
            cap.release()

        return {
            "content_scores": content_scores,
            "histogram_scores": histogram_scores,
            "sampled_frames": np.asarray(sampled_frames, dtype=np.int32),
            "sample_step": step,
        }

    def _detect_with_pyscenedetect_detectors(
        self,
        video_path: str,
        detectors,
        total_frames: int,
        fps: float,
        progress_callback: Optional[Callable[[int], None]] = None,
        collect_scores: bool = False,
    ) -> dict:
        """Run multiple PySceneDetect detectors in a single decode pass."""
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"????????????: {video_path}")

        results = {name: [] for name, _detector in detectors}
        content_scores = np.zeros(total_frames, dtype=np.float32)
        histogram_scores = np.zeros(total_frames, dtype=np.float32)
        frame_idx = -1
        last_reported = 5
        fps = fps or 25.0
        previous_hist = None
        previous_gray = None
        previous_content_channels = None

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                analysis_frame = self._resize_for_analysis(frame)
                timecode = FrameTimecode(frame_idx, fps)
                content_score = None
                for name, detector in detectors:
                    for cut in detector.process_frame(timecode, analysis_frame):
                        results[name].append(self._frame_num(cut))
                    if collect_scores and content_score is None:
                        detector_score = getattr(detector, "_frame_score", None)
                        if detector_score is not None:
                            content_score = float(detector_score or 0.0)

                if collect_scores and frame_idx < total_frames:
                    if content_score is None:
                        content_score, previous_content_channels = self._content_score_for_frame(
                            analysis_frame,
                            previous_content_channels,
                            already_resized=True,
                        )
                    content_scores[frame_idx] = content_score
                    hist_score, previous_hist, previous_gray = self._histogram_score_for_frame(
                        analysis_frame,
                        previous_hist,
                        previous_gray,
                        already_resized=True,
                    )
                    histogram_scores[frame_idx] = hist_score

                if progress_callback and total_frames > 0:
                    pct = 5 + int((frame_idx / total_frames) * 30)
                    if pct > last_reported:
                        progress_callback(min(pct, 35))
                        last_reported = pct

            if frame_idx >= 0:
                last_timecode = FrameTimecode(frame_idx, fps)
                for name, detector in detectors:
                    for cut in detector.post_process(last_timecode):
                        results[name].append(self._frame_num(cut))
        finally:
            cap.release()

        return {
            "cuts": {
                name: sorted(set(frame for frame in detector_cuts if frame > 0))
                for name, detector_cuts in results.items()
            },
            "features": {
                "content_scores": content_scores,
                "histogram_scores": histogram_scores,
            },
        }

    def _frame_num(self, timecode) -> int:
        frame_num = getattr(timecode, "frame_num", None)
        if frame_num is not None:
            return int(frame_num)
        return int(timecode.get_frames())

    def _content_score_for_frame(self, frame, previous_channels, already_resized: bool = False):
        small = frame if already_resized else self._resize_for_analysis(frame)
        hue, sat, lum = cv2.split(cv2.cvtColor(small, cv2.COLOR_BGR2HSV))

        if previous_channels is None:
            return 0.0, (hue, sat, lum)

        previous_hue, previous_sat, previous_lum = previous_channels
        score = float(
            (
                np.mean(cv2.absdiff(hue, previous_hue))
                + np.mean(cv2.absdiff(sat, previous_sat))
                + np.mean(cv2.absdiff(lum, previous_lum))
            )
            / 3.0
        )
        return score, (hue, sat, lum)

    def _histogram_score_for_frame(
        self,
        frame,
        previous_hist,
        previous_gray,
        already_resized: bool = False,
    ):
        small = frame if already_resized else self._resize_for_analysis(frame)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

        score = 0.0
        if previous_hist is not None and previous_gray is not None:
            hist_diff = cv2.compareHist(previous_hist, hist, cv2.HISTCMP_BHATTACHARYYA)
            pixel_diff = float(np.mean(cv2.absdiff(previous_gray, gray)) / 255.0)
            score = float(hist_diff * 0.75 + pixel_diff * 0.25)
        return score, hist, gray

    def _detect_content_from_scores(
        self,
        content_scores,
        threshold: float,
        min_scene_len: int,
    ) -> List[int]:
        cuts: List[int] = []
        last_cut = -min_scene_len
        for frame_idx, score in enumerate(content_scores):
            if score >= threshold and frame_idx - last_cut >= min_scene_len:
                cuts.append(frame_idx)
                last_cut = frame_idx
        return cuts

    def _detect_adaptive_from_scores(
        self,
        content_scores,
        adaptive_threshold: float,
        min_scene_len: int,
    ) -> List[int]:
        window_width = 2
        min_content_val = 15.0
        cuts: List[int] = []
        last_cut = 0
        scores = np.asarray(content_scores, dtype=np.float32)
        for target_idx in range(window_width, len(scores) - window_width):
            target_score = float(scores[target_idx])
            window = np.concatenate(
                (
                    scores[target_idx - window_width:target_idx],
                    scores[target_idx + 1:target_idx + window_width + 1],
                )
            )
            average = float(np.mean(window)) if window.size else 0.0
            if abs(average) < 0.00001:
                adaptive_ratio = 255.0 if target_score >= min_content_val else 0.0
            else:
                adaptive_ratio = min(target_score / average, 255.0)

            check_idx = target_idx + window_width
            if (
                adaptive_ratio >= adaptive_threshold
                and target_score >= min_content_val
                and check_idx - last_cut >= min_scene_len
            ):
                cuts.append(target_idx)
                last_cut = target_idx
        return cuts

    def _detect_adaptive_from_sampled_scores(
        self,
        content_scores,
        sampled_frames,
        adaptive_threshold: float,
        min_scene_len: int,
    ) -> List[int]:
        window_width = 2
        min_content_val = 15.0
        cuts: List[int] = []
        last_cut = 0
        sampled_frames = np.asarray(sampled_frames, dtype=np.int32)
        scores = np.asarray(content_scores, dtype=np.float32)
        if len(sampled_frames) < 1 + (2 * window_width):
            return cuts

        sampled_scores = np.asarray(
            [self._score_at(scores, int(frame_idx)) for frame_idx in sampled_frames],
            dtype=np.float32,
        )
        for sample_pos in range(window_width, len(sampled_scores) - window_width):
            target_score = float(sampled_scores[sample_pos])
            window = np.concatenate(
                (
                    sampled_scores[sample_pos - window_width:sample_pos],
                    sampled_scores[sample_pos + 1:sample_pos + window_width + 1],
                )
            )
            average = float(np.mean(window)) if window.size else 0.0
            if abs(average) < 0.00001:
                adaptive_ratio = 255.0 if target_score >= min_content_val else 0.0
            else:
                adaptive_ratio = min(target_score / average, 255.0)

            cut_frame = int(sampled_frames[sample_pos])
            if (
                adaptive_ratio >= adaptive_threshold
                and target_score >= min_content_val
                and cut_frame - last_cut >= min_scene_len
            ):
                cuts.append(cut_frame)
                last_cut = cut_frame

        return cuts

    def _detect_histogram_from_scores(
        self,
        histogram_scores,
        min_scene_len: int,
        threshold: Optional[float] = None,
    ) -> List[int]:
        cuts: List[int] = []
        last_cut = -min_scene_len
        threshold = self.settings.histogram_threshold if threshold is None else float(threshold)
        for frame_idx, score in enumerate(histogram_scores):
            if (
                score >= threshold
                and frame_idx - last_cut >= min_scene_len
            ):
                cuts.append(frame_idx)
                last_cut = frame_idx
        return cuts

    def _detect_with_histograms(
        self,
        video_path: str,
        total_frames: int,
        min_scene_len: int,
        progress_callback: Optional[Callable[[int], None]] = None,
    ) -> List[int]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"????????: {video_path}")

        cuts: List[int] = []
        previous_hist = None
        previous_gray = None
        last_cut = -min_scene_len
        frame_idx = -1
        last_reported = 36

        while True:
            ret, frame = cap.read()
            if not ret:
                break

            frame_idx += 1
            small = self._resize_for_analysis(frame)
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
            gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)

            if previous_hist is not None and previous_gray is not None:
                hist_diff = cv2.compareHist(previous_hist, hist, cv2.HISTCMP_BHATTACHARYYA)
                pixel_diff = float(np.mean(cv2.absdiff(previous_gray, gray)) / 255.0)
                score = hist_diff * 0.75 + pixel_diff * 0.25

                if (
                    score >= self.settings.histogram_threshold
                    and frame_idx - last_cut >= min_scene_len
                ):
                    cuts.append(frame_idx)
                    last_cut = frame_idx

            previous_hist = hist
            previous_gray = gray

            if progress_callback and total_frames > 0:
                pct = 36 + int((frame_idx / total_frames) * 10)
                if pct > last_reported:
                    progress_callback(min(pct, 46))
                    last_reported = pct

        cap.release()
        return cuts

    def _merge_similar_shots(
        self,
        video_path: str,
        shots: List[Tuple[int, int]],
        fps: float,
    ) -> List[Tuple[int, int]]:
        if len(shots) < 2:
            self.similar_merge_count = 0
            return shots

        threshold = max(0.0, float(self.settings.merge_similarity_threshold))
        max_short_len = max(1, int(round(self.settings.merge_max_shot_seconds * fps)))
        needed_frames = sorted(
            set(
                frame_idx
                for start, end in shots
                for frame_idx in (start, end)
                if frame_idx >= 0
            )
        )
        histograms = self._read_histograms_for_frames(video_path, needed_frames)
        if not histograms:
            self.similar_merge_count = 0
            return shots

        merged: List[Tuple[int, int]] = []
        current_start, current_end = shots[0]
        self.similar_merge_count = 0

        for next_start, next_end in shots[1:]:
            current_len = current_end - current_start + 1
            next_len = next_end - next_start + 1
            should_check = min(current_len, next_len) <= max_short_len

            if should_check:
                current_hist = histograms.get(current_end)
                next_hist = histograms.get(next_start)
                if current_hist is not None and next_hist is not None:
                    distance = self._histogram_distance_from_histograms(current_hist, next_hist)
                    if distance <= threshold:
                        current_end = next_end
                        self.similar_merge_count += 1
                        continue

            merged.append((current_start, current_end))
            current_start, current_end = next_start, next_end

        merged.append((current_start, current_end))
        return merged

    def _read_frame(self, cap: cv2.VideoCapture, frame_idx: int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        return frame if ret else None

    def _histogram_distance(self, first: np.ndarray, second: np.ndarray) -> float:
        first_hist = self._hsv_histogram(first)
        second_hist = self._hsv_histogram(second)
        return self._histogram_distance_from_histograms(first_hist, second_hist)

    def _histogram_distance_from_histograms(
        self,
        first_hist: np.ndarray,
        second_hist: np.ndarray,
    ) -> float:
        return float(cv2.compareHist(first_hist, second_hist, cv2.HISTCMP_BHATTACHARYYA))

    def _hsv_histogram(self, frame: np.ndarray) -> np.ndarray:
        small = self._resize_for_analysis(frame)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
        return hist

    def _read_histograms_for_frames(self, video_path: str, frame_indices) -> dict:
        targets = sorted(set(int(frame_idx) for frame_idx in frame_indices if frame_idx >= 0))
        if not targets:
            return {}

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return {}

        histograms = {}
        frame_idx = -1
        target_pos = 0
        try:
            while target_pos < len(targets):
                if not cap.grab():
                    break
                frame_idx += 1

                while target_pos < len(targets) and targets[target_pos] < frame_idx:
                    target_pos += 1
                if target_pos >= len(targets):
                    break
                if targets[target_pos] != frame_idx:
                    continue

                ret, frame = cap.retrieve()
                if ret:
                    histograms[frame_idx] = self._hsv_histogram(frame)
                target_pos += 1
        finally:
            cap.release()

        return histograms

    def _resize_for_analysis(self, frame: np.ndarray) -> np.ndarray:
        width = frame.shape[1]
        target_width = max(160, int(self.settings.analysis_width))
        if width <= target_width:
            return frame
        scale = target_width / width
        target_height = max(1, int(frame.shape[0] * scale))
        return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)

    def _refine_sampled_cuts(
        self,
        video_path: str,
        cuts: List[int],
        total_frames: int,
        sample_step: int,
    ) -> List[int]:
        if not cuts or sample_step <= 1:
            return cuts

        windows = {}
        needed_frames = set()
        for cut in cuts:
            start = max(1, int(cut) - int(sample_step) + 1)
            end = min(total_frames - 1, int(cut) + 1)
            windows[int(cut)] = (start, end)
            needed_frames.update(range(start - 1, end + 1))

        histograms = self._read_histograms_for_frames(video_path, needed_frames)
        if not histograms:
            return cuts

        refined: List[int] = []
        for cut, (start, end) in windows.items():
            best_frame = int(cut)
            best_score = -1.0
            for frame_idx in range(start, end + 1):
                previous_hist = histograms.get(frame_idx - 1)
                current_hist = histograms.get(frame_idx)
                if previous_hist is None or current_hist is None:
                    continue
                score = self._histogram_distance_from_histograms(previous_hist, current_hist)
                if score > best_score:
                    best_score = score
                    best_frame = frame_idx
            refined.append(best_frame)

        return sorted(set(refined))

    def _merge_cuts(
        self,
        cuts: List[int],
        total_frames: int,
        min_scene_len: int,
    ) -> List[int]:
        merged: List[int] = []
        last_cut = 0

        for cut in sorted(set(int(cut) for cut in cuts)):
            if cut <= 0 or cut >= total_frames - 1:
                continue
            if cut - last_cut < min_scene_len:
                continue
            if total_frames - cut < max(2, min_scene_len // 2):
                continue
            merged.append(cut)
            last_cut = cut

        return merged

    def _cuts_to_shots(
        self,
        cuts: List[int],
        total_frames: int,
    ) -> List[Tuple[int, int]]:
        starts = [0] + cuts
        ends = [cut - 1 for cut in cuts] + [total_frames - 1]
        return [
            (start, end)
            for start, end in zip(starts, ends)
            if end >= start
        ]

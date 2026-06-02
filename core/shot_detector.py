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
    analysis_width: int = 320
    merge_similar_shots: bool = True
    merge_similarity_threshold: float = 0.08
    merge_max_shot_seconds: float = 1.0
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
        analysis_width: int = 320,
        merge_similar_shots: bool = True,
        merge_similarity_threshold: float = 0.08,
        merge_max_shot_seconds: float = 1.0,
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
            merge_similar_shots=merge_similar_shots,
            merge_similarity_threshold=merge_similarity_threshold,
            merge_max_shot_seconds=merge_max_shot_seconds,
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
        cuts: List[int] = []
        pyscene_detectors = []
        if mode in {"content", "hybrid"}:
            pyscene_detectors.append(
                (
                    "content",
                    ContentDetector(
                        threshold=self.settings.content_threshold,
                        min_scene_len=min_scene_len,
                    ),
                )
            )

        if mode in {"adaptive", "hybrid"}:
            pyscene_detectors.append(
                (
                    "adaptive",
                    AdaptiveDetector(
                        adaptive_threshold=self.settings.adaptive_threshold,
                        min_scene_len=min_scene_len,
                    ),
                )
            )

        feature_payload = self._detect_with_pyscenedetect_detectors(
            video_path=video_path,
            detectors=pyscene_detectors,
            total_frames=total_frames,
            fps=fps,
            progress_callback=progress_callback,
            collect_scores=True,
        )
        pyscene_results = feature_payload["cuts"]
        features = feature_payload["features"]

        for name, detector_cuts in pyscene_results.items():
            self.last_cut_candidates[name] = detector_cuts
            cuts.extend(detector_cuts)

        if self.settings.histogram_enabled and mode in {"histogram", "hybrid"}:
            histogram_cuts = self._detect_histogram_from_scores(
                features["histogram_scores"],
                min_scene_len,
            )
            self.last_cut_candidates["histogram"] = histogram_cuts
            cuts.extend(histogram_cuts)

        if self.settings.feature_cache_enabled:
            saved_path = feature_cache.save(
                video_path=video_path,
                total_frames=total_frames,
                fps=fps,
                analysis_width=self.settings.analysis_width,
                content_scores=features["content_scores"],
                histogram_scores=features["histogram_scores"],
            )
            self.feature_cache_path = str(saved_path) if saved_path else None

        return cuts

    def _cuts_from_features(self, features: dict, min_scene_len: int, mode: str) -> List[int]:
        cuts: List[int] = []
        content_scores = features["content_scores"]
        histogram_scores = features["histogram_scores"]

        if mode in {"content", "hybrid"}:
            content_cuts = self._detect_content_from_scores(
                content_scores,
                self.settings.content_threshold,
                min_scene_len,
            )
            self.last_cut_candidates["content"] = content_cuts
            cuts.extend(content_cuts)

        if mode in {"adaptive", "hybrid"}:
            adaptive_cuts = self._detect_adaptive_from_scores(
                content_scores,
                self.settings.adaptive_threshold,
                min_scene_len,
            )
            self.last_cut_candidates["adaptive"] = adaptive_cuts
            cuts.extend(adaptive_cuts)

        if self.settings.histogram_enabled and mode in {"histogram", "hybrid"}:
            histogram_cuts = self._detect_histogram_from_scores(
                histogram_scores,
                min_scene_len,
            )
            self.last_cut_candidates["histogram"] = histogram_cuts
            cuts.extend(histogram_cuts)

        return cuts

    def _probe_video(self, video_path: str) -> Tuple[int, float]:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            raise ValueError(f"无法打开视频文件: {video_path}")
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
            raise ValueError(f"鏃犳硶鎵撳紑瑙嗛鏂囦欢: {video_path}")

        results = {name: [] for name, _detector in detectors}
        content_scores = np.zeros(total_frames, dtype=np.float32)
        histogram_scores = np.zeros(total_frames, dtype=np.float32)
        feature_detector = ContentDetector()
        frame_idx = -1
        last_reported = 5
        fps = fps or 25.0
        previous_hist = None
        previous_gray = None

        try:
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                frame_idx += 1
                timecode = FrameTimecode(frame_idx, fps)
                for name, detector in detectors:
                    for cut in detector.process_frame(timecode, frame):
                        results[name].append(self._frame_num(cut))

                if collect_scores and frame_idx < total_frames:
                    feature_detector.process_frame(timecode, frame)
                    content_scores[frame_idx] = float(feature_detector._frame_score or 0.0)
                    hist_score, previous_hist, previous_gray = self._histogram_score_for_frame(
                        frame,
                        previous_hist,
                        previous_gray,
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

    def _histogram_score_for_frame(self, frame, previous_hist, previous_gray):
        small = self._resize_for_analysis(frame)
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

    def _detect_histogram_from_scores(
        self,
        histogram_scores,
        min_scene_len: int,
    ) -> List[int]:
        cuts: List[int] = []
        last_cut = -min_scene_len
        for frame_idx, score in enumerate(histogram_scores):
            if (
                score >= self.settings.histogram_threshold
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
            raise ValueError(f"无法打开视频文件: {video_path}")

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

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            self.similar_merge_count = 0
            return shots

        threshold = max(0.0, float(self.settings.merge_similarity_threshold))
        max_short_len = max(1, int(round(self.settings.merge_max_shot_seconds * fps)))
        merged: List[Tuple[int, int]] = []
        current_start, current_end = shots[0]
        self.similar_merge_count = 0

        try:
            for next_start, next_end in shots[1:]:
                current_len = current_end - current_start + 1
                next_len = next_end - next_start + 1
                should_check = min(current_len, next_len) <= max_short_len

                if should_check:
                    current_frame = self._read_frame(cap, current_end)
                    next_frame = self._read_frame(cap, next_start)
                    if current_frame is not None and next_frame is not None:
                        distance = self._histogram_distance(current_frame, next_frame)
                        if distance <= threshold:
                            current_end = next_end
                            self.similar_merge_count += 1
                            continue

                merged.append((current_start, current_end))
                current_start, current_end = next_start, next_end
        finally:
            cap.release()

        merged.append((current_start, current_end))
        return merged

    def _read_frame(self, cap: cv2.VideoCapture, frame_idx: int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        return frame if ret else None

    def _histogram_distance(self, first: np.ndarray, second: np.ndarray) -> float:
        first_hist = self._hsv_histogram(first)
        second_hist = self._hsv_histogram(second)
        return float(cv2.compareHist(first_hist, second_hist, cv2.HISTCMP_BHATTACHARYYA))

    def _hsv_histogram(self, frame: np.ndarray) -> np.ndarray:
        small = self._resize_for_analysis(frame)
        hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
        cv2.normalize(hist, hist, alpha=0, beta=1, norm_type=cv2.NORM_MINMAX)
        return hist

    def _resize_for_analysis(self, frame: np.ndarray) -> np.ndarray:
        width = frame.shape[1]
        target_width = max(160, int(self.settings.analysis_width))
        if width <= target_width:
            return frame
        scale = target_width / width
        target_height = max(1, int(frame.shape[0] * scale))
        return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_AREA)

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

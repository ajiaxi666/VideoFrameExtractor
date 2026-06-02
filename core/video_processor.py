import cv2


class VideoProcessor:
    """Basic video reader used by the UI and exporter."""

    def __init__(self, video_path: str):
        self.video_path = video_path
        self.cap = None
        self.total_frames = 0
        self.fps = 0.0
        self.width = 0
        self.height = 0

    def open(self):
        self.cap = cv2.VideoCapture(self.video_path)
        if not self.cap.isOpened():
            raise ValueError(f"无法打开视频文件: {self.video_path}")

        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.fps = float(self.cap.get(cv2.CAP_PROP_FPS) or 0)
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

    def close(self):
        if self.cap:
            self.cap.release()
            self.cap = None

    def get_frame(self, frame_idx: int):
        if not self.cap:
            self.open()

        self.cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = self.cap.read()

        if not ret:
            raise ValueError(f"无法读取第 {frame_idx} 帧")

        return frame

    def get_duration(self) -> float:
        if self.total_frames == 0:
            self.open()
        return self.total_frames / self.fps if self.fps > 0 else 0.0

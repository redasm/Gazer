import cv2
try:
    import mediapipe as mp
except ImportError:
    mp = None

import time
import logging

logger = logging.getLogger("GazerVision")

class GazerVision:
    """
    视觉感知模块
    负责检测用户在场及注意力（姿态）
    """
    def __init__(self):
        if mp:
            self.mp_face_detection = mp.solutions.face_detection
            self.face_detection = self.mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.5)
        else:
            self.face_detection = None
            logger.warning("MediaPipe not found, face detection will be disabled.")
        self.cap = None

    def start_capture(self, device_id=0):
        self.cap = cv2.VideoCapture(device_id)
        if not self.cap.isOpened():
            logger.error("Could not open camera")
            return False
        return True

    def detect_user(self):
        """检测主要用户是否在场"""
        if not self.cap or not self.face_detection:
            # 如果没有摄像头或没有模型，模拟检测（用于测试环境）
            return False, 0
        
        success, image = self.cap.read()
        if not success:
            return False, 0

        # 转换为 RGB 供 Mediapipe 使用
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        results = self.face_detection.process(image_rgb)

        if results.detections:
            return True, len(results.detections)
        
        return False, 0

    def get_attention_level(self):
        """
        判断注意力水平
        0: 不在场, 1: 在场但不直接关注, 2: 正对摄像头关注
        """
        # 简化逻辑：如果在场即为关注，后续将结合头部姿态估计
        is_present, count = self.detect_user()
        if not is_present:
            return 0
        return 1

    def release(self):
        if self.cap:
            self.cap.release()


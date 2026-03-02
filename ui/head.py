from PySide6.QtWidgets import QApplication, QMainWindow, QWidget, QVBoxLayout, QLabel
from PySide6.QtCore import Qt, QTimer, QPoint, QEasingCurve, QPropertyAnimation, Property
from PySide6.QtGui import QPainter, QColor, QRadialGradient, QBrush
import sys
import random
from runtime.config_manager import config

class GazerEye(QWidget):
    """
    具有呼吸感和表现力的 Gazer 之眼
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(160, 160)
        self._pupil_pos = QPoint(80, 80)
        self._eye_open = 1.0
        self._glow_intensity = 0.8
        
        # 呼吸灯定时器
        self.glow_timer = QTimer()
        self.glow_timer.timeout.connect(self._update_glow)
        self.glow_timer.start(50)
        self._glow_step = 0.02
        
    def _update_glow(self):
        self._glow_intensity += self._glow_step
        if self._glow_intensity > 1.0 or self._glow_intensity < 0.6:
            self._glow_step *= -1
        self.update()

    @Property(float)
    def eye_open(self):
        return self._eye_open
        
    @eye_open.setter
    def eye_open(self, value):
        self._eye_open = value
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        # 背景（球形感）
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(15, 15, 20))
        painter.drawEllipse(5, 5, 150, 150)
        
        # 计算眼睛高度（睁眼/闭眼）
        h = 140 * self._eye_open
        y_offset = (140 - h) / 2
        
        if h > 2:
            # 核心光效 (动态呼吸)
            color_list = config.get("visual.eye_color", [0, 200, 255])
            base_color = QColor(*color_list)
            
            color_val = int(200 * self._glow_intensity)
            gradient = QRadialGradient(self._pupil_pos, 70)
            gradient.setColorAt(0, QColor(0, color_val, base_color.blue(), 200))
            gradient.setColorAt(0.6, QColor(0, 50, 150, 100))
            gradient.setColorAt(1, QColor(0, 20, 50, 0))
            
            painter.setBrush(gradient)
            painter.drawEllipse(10, 10 + y_offset, 140, h)
            
            # 瞳孔/高光
            painter.setBrush(QColor(255, 255, 255, 180))
            painter.drawEllipse(self._pupil_pos.x()-8, self._pupil_pos.y()-8 + y_offset/2, 16, 16)
            
            # 装饰外圈
            painter.setPen(QColor(0, 255, 255, 50))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(10, 10 + y_offset, 140, h)

class GazerHeadWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Gazer Project")
        self.setFixedSize(450, 320)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        
        # 毛玻璃感底色
        self.bg_frame = QWidget(self)
        self.bg_frame.setGeometry(0, 0, 450, 320)
        self.bg_frame.setStyleSheet("background-color: rgba(5, 10, 20, 220); border-radius: 25px; border: 1px solid rgba(0, 255, 255, 40);")
        
        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(30, 30, 30, 30)
        
        from PySide6.QtWidgets import QHBoxLayout
        self.eye_layout = QHBoxLayout()
        self.left_eye = GazerEye()
        self.right_eye = GazerEye()
        self.eye_layout.addWidget(self.left_eye)
        self.eye_layout.addWidget(self.right_eye)
        self.layout.addLayout(self.eye_layout)
        
        self.status_label = QLabel("GAZER SYSTEM ACTIVE")
        self.status_label.setStyleSheet("color: #00ffff; font-family: 'Segoe UI', sans-serif; font-size: 12px; font-weight: bold; letter-spacing: 2px;")
        self.status_label.setAlignment(Qt.AlignCenter)
        self.layout.addWidget(self.status_label)
        
        # 定时器与控制
        self.blink_timer = QTimer()
        self.blink_timer.timeout.connect(self.blink)
        self.blink_timer.start(config.get("visual.blink_interval", 3000))
        
        self.move_timer = QTimer()
        self.move_timer.timeout.connect(self.random_look)
        self.move_timer.start(5000)
        
        self.queue = None
        self.poll_timer = QTimer()
        self.poll_timer.timeout.connect(self.check_queue)

    def check_queue(self):
        # 实时同步文件变更
        config.check_reload()
        
        if self.queue and not self.queue.empty():
            try:
                msg = self.queue.get_nowait()
                if "status" in msg:
                    self.status_label.setText(msg["status"].upper())
                if "emotion" in msg:
                    # TODO: 实现更多情绪表达逻辑
                    pass
            except Exception:
                pass

    def blink(self):
        self.animation = QPropertyAnimation(self.left_eye, b"eye_open")
        self.animation.setDuration(150)
        self.animation.setStartValue(1.0)
        self.animation.setKeyValueAt(0.5, 0.0)
        self.animation.setEndValue(1.0)
        self.animation.setEasingCurve(QEasingCurve.InOutQuad)
        
        self.animation2 = QPropertyAnimation(self.right_eye, b"eye_open")
        self.animation2.setDuration(150)
        self.animation2.setStartValue(1.0)
        self.animation2.setKeyValueAt(0.5, 0.0)
        self.animation2.setEndValue(1.0)
        self.animation2.setEasingCurve(QEasingCurve.InOutQuad)
        
        self.animation.start()
        self.animation2.start()

    def random_look(self):
        tx = random.randint(60, 100)
        ty = random.randint(60, 100)
        self.left_eye._pupil_pos = QPoint(tx, ty)
        self.right_eye._pupil_pos = QPoint(tx, ty)
        self.update()

def run_head(command_queue=None):
    app = QApplication.instance() or QApplication(sys.argv)
    window = GazerHeadWindow()
    if command_queue:
        window.queue = command_queue
        window.poll_timer.start(100)
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    run_head()

import sys
import os
import logging
import threading
from pathlib import Path
from urllib.parse import urlparse

from PyQt5.QtCore import (
    Qt, QTimer, QPropertyAnimation, QRect, 
    QEvent, QObject, QMetaObject, Q_ARG, QThread, pyqtSlot
)
from PyQt5.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, 
    QHBoxLayout, QLabel, QPushButton, QSlider, QLineEdit,
    QComboBox, QCheckBox, QMessageBox, QFrame,
    QStackedWidget, QTabBar, QStyleOptionSlider, QStyle,
    QGraphicsOpacityEffect, QSizePolicy, QScrollArea, QGridLayout,
    QSystemTrayIcon, QMenu, QAction
)
from PyQt5.QtGui import (
    QPainter, QColor, QFont, QMouseEvent, QMovie, QBrush, QPen, 
    QLinearGradient, QTransform, QPainterPath, QIcon, QPixmap
)

from src.utils.config_manager import ConfigManager
import queue
import time
import numpy as np
from typing import Optional, Callable
from pynput import keyboard as pynput_keyboard
from abc import ABCMeta
from src.display.base_display import BaseDisplay
import json

# Define configuration file path
CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "config.json"


def restart_program():
    """Restart the current Python program, supports packaged environment."""
    try:
        python = sys.executable
        print(f"Attempting to restart with command: {python} {sys.argv}")

        # Try to close Qt application, although execv will take over, this is more standard
        app = QApplication.instance()
        if app:
            app.quit()

        # Use different restart method in packaged environment
        if getattr(sys, 'frozen', False):
            # In packaged environment, use subprocess to start new process
            import subprocess

            # Build complete command line
            if sys.platform.startswith('win'):
                # Use detached to create independent process on Windows
                executable = os.path.abspath(sys.executable)
                subprocess.Popen([executable] + sys.argv[1:],
                                 creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
            else:
                # Linux/Mac
                executable = os.path.abspath(sys.executable)
                subprocess.Popen([executable] + sys.argv[1:],
                                 start_new_session=True)

            # Exit current process
            sys.exit(0)
        else:
            # Non-packaged environment, use os.execv
            os.execv(python, [python] + sys.argv)
    except Exception as e:
        print(f"Failed to restart program: {e}")
        logging.getLogger("Display").error(f"Failed to restart program: {e}", exc_info=True)
        # If restart fails, can choose to exit or notify user
        sys.exit(1)  # Or show an error message box


# Create compatible metaclass
class CombinedMeta(type(QObject), ABCMeta):
    pass


class GuiDisplay(BaseDisplay, QObject, metaclass=CombinedMeta):
    def __init__(self):
        # Important: call super() to handle multiple inheritance
        super().__init__()
        QObject.__init__(self)  # Call QObject initialization

        # Initialize logging
        self.logger = logging.getLogger("Display")
        
        self.app = None
        self.root = None
        
        # Pre-initialized variables
        self.status_label = None
        self.emotion_label = None
        self.tts_text_label = None
        self.volume_scale = None
        self.manual_btn = None
        self.abort_btn = None
        self.auto_btn = None
        self.mode_btn = None
        self.mute = None
        self.stackedWidget = None
        self.nav_tab_bar = None
        
        # Add emotion animation object
        self.emotion_movie = None
        # New emotion animation effect related variables
        self.emotion_effect = None  # Emotion opacity effect
        self.emotion_animation = None  # Emotion animation object
        self.next_emotion_path = None  # Next emotion to display
        self.is_emotion_animating = False  # Whether emotion transition animation is in progress
        
        # Volume control related
        self.volume_label = None  # Volume percentage label
        self.volume_control_available = False  # Whether system volume control is available
        self.volume_controller_failed = False  # Mark if volume control failed
        
        # Microphone visualization related
        self.mic_visualizer = None  # Microphone visualization component
        self.mic_timer = None  # Microphone volume update timer
        self.is_listening = False  # Whether currently listening
        
        # Settings page controls
        self.wakeWordEnableSwitch = None
        self.wakeWordsLineEdit = None
        self.saveSettingsButton = None
        # New network and device ID control references
        self.deviceIdLineEdit = None
        self.wsProtocolComboBox = None
        self.wsAddressLineEdit = None
        self.wsTokenLineEdit = None
        # New OTA address control references
        self.otaProtocolComboBox = None
        self.otaAddressLineEdit = None
        # Home Assistant control references
        self.haProtocolComboBox = None
        self.ha_server = None
        self.ha_port = None
        self.ha_key = None
        self.Add_ha_devices = None

        self.is_muted = False
        self.pre_mute_volume = self.current_volume
        
        # Dialogue mode flag
        self.auto_mode = False

        # Callback functions
        self.button_press_callback = None
        self.button_release_callback = None
        self.status_update_callback = None
        self.text_update_callback = None
        self.emotion_update_callback = None
        self.mode_callback = None
        self.auto_callback = None
        self.abort_callback = None
        self.send_text_callback = None

        # Update queue
        self.update_queue = queue.Queue()

        # Running flag
        self._running = True

        # Keyboard listener
        self.keyboard_listener = None
        # Add key state set
        self.pressed_keys = set()

        # Swipe gesture related
        self.last_mouse_pos = None
        
        # Save timer references to prevent destruction
        self.update_timer = None
        self.volume_update_timer = None
        
        # Animation related
        self.current_effect = None
        self.current_animation = None
        self.animation = None
        self.fade_widget = None
        self.animated_widget = None
        
        # Check if system volume control is available
        self.volume_control_available = (hasattr(self, 'volume_controller') and
                                         self.volume_controller is not None)
        
        # Try to get system volume once to check if volume control works
        self.get_current_volume()

        # New iotPage related variables
        self.devices_list = []
        self.device_labels = {}
        self.history_title = None
        self.iot_card = None
        self.ha_update_timer = None
        self.device_states = {}
        
        # New system tray related variables
        self.tray_icon = None
        self.tray_menu = None
        self.current_status = ""  # Current status, used to determine color changes
        self.is_connected = True  # Connection status flag

    def eventFilter(self, source, event):
        if source == self.volume_scale and event.type() == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                slider = self.volume_scale
                opt = QStyleOptionSlider()
                slider.initStyleOption(opt)
                
                # Get slider handle and track rectangle
                handle_rect = slider.style().subControlRect(
                    QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, slider)
                groove_rect = slider.style().subControlRect(
                    QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, slider)

                # If clicked on handle, let default processor handle dragging
                if handle_rect.contains(event.pos()):
                    return False 

                # Calculate position relative to track
                if slider.orientation() == Qt.Horizontal:
                    # Ensure click is within valid track range
                    if (event.pos().x() < groove_rect.left() or
                            event.pos().x() > groove_rect.right()):
                        return False  # Click outside track
                    pos = event.pos().x() - groove_rect.left()
                    max_pos = groove_rect.width()
                else:
                    if (event.pos().y() < groove_rect.top() or
                            event.pos().y() > groove_rect.bottom()):
                        return False  # Click outside track
                    pos = groove_rect.bottom() - event.pos().y()
                    max_pos = groove_rect.height()

                if max_pos > 0:  # Avoid division by zero
                    value_range = slider.maximum() - slider.minimum()
                    # Calculate new value based on click position
                    new_value = slider.minimum() + round(
                        (value_range * pos) / max_pos)
                    
                    # Set slider value directly
                    slider.setValue(int(new_value))
                    
                    return True  # Event handled
        
        return super().eventFilter(source, event)

    def _setup_navigation(self):
        """Set navigation tab bar (QTabBar)"""
        # Use addTab to add tabs
        self.nav_tab_bar.addTab("Chat")  # index 0
        self.nav_tab_bar.addTab("Device Management")  # index 1
        self.nav_tab_bar.addTab("Settings")  # index 2

        # Connect QTabBar's currentChanged signal to handler function
        self.nav_tab_bar.currentChanged.connect(self._on_navigation_index_changed)

        # Set default selected item (by index)
        self.nav_tab_bar.setCurrentIndex(0) # Default select first tab

    def _on_navigation_index_changed(self, index: int):
        """Handle navigation tab change (by index)"""
        # Map back to routeKey for reuse of animation and loading logic
        index_to_routeKey = {0: "mainInterface", 1: "iotInterface", 2: "settingInterface"}
        routeKey = index_to_routeKey.get(index)

        if routeKey is None:
            self.logger.warning(f"Unknown navigation index: {index}")
            return

        target_index = index # Directly use index
        if target_index == self.stackedWidget.currentIndex():
            return

        current_widget = self.stackedWidget.currentWidget()
        self.stackedWidget.setCurrentIndex(target_index)
        new_widget = self.stackedWidget.currentWidget()

        # If switching to settings page, load settings
        if routeKey == "settingInterface":
            self._load_settings()

        # If switching to device management page, load devices
        if routeKey == "iotInterface":
            self._load_iot_devices()

    def set_callbacks(
        self,
        press_callback: Optional[Callable] = None,
        release_callback: Optional[Callable] = None,
        status_callback: Optional[Callable] = None,
        text_callback: Optional[Callable] = None,
        emotion_callback: Optional[Callable] = None,
        mode_callback: Optional[Callable] = None,
        auto_callback: Optional[Callable] = None,
        abort_callback: Optional[Callable] = None,
        send_text_callback: Optional[Callable] = None,
    ):
        """Set callback functions"""
        self.button_press_callback = press_callback
        self.button_release_callback = release_callback
        self.status_update_callback = status_callback
        self.text_update_callback = text_callback
        self.emotion_update_callback = emotion_callback
        self.mode_callback = mode_callback
        self.auto_callback = auto_callback
        self.abort_callback = abort_callback
        self.send_text_callback = send_text_callback

        # Add status listener to application's state change callback after initialization
        # This way, we can update system tray icon when device state changes
        from src.application import Application
        app = Application.get_instance()
        if app:
            app.on_state_changed_callbacks.append(self._on_state_changed)
            
    def _on_state_changed(self, state):
        """Listen for device state changes"""
        # Set connection status flag
        from src.constants.constants import DeviceState
        
        # Check if connecting or already connected
        # (CONNECTING, LISTENING, SPEAKING means connected)
        if state == DeviceState.CONNECTING:
            self.is_connected = True
        elif state in [DeviceState.LISTENING, DeviceState.SPEAKING]:
            self.is_connected = True
        elif state == DeviceState.IDLE:
            # Get protocol instance from application to check WebSocket connection status
            from src.application import Application
            app = Application.get_instance()
            if app and app.protocol:
                # Check if protocol is connected
                self.is_connected = app.protocol.is_audio_channel_opened()
            else:
                self.is_connected = False
        
        # Status update processing has already been completed in update_status method

    def _process_updates(self):
        """Handle update queue"""
        if not self._running:
            return
            
        try:
            while True:
                try:
                    # Non-blocking way to get update
                    update_func = self.update_queue.get_nowait()
                    update_func()
                    self.update_queue.task_done()
                except queue.Empty:
                    break
        except Exception as e:
            self.logger.error(f"Error occurred while processing update queue: {e}")

    def _on_manual_button_press(self):
        """Handle manual mode button press event"""
        try:
            # Update button text to "Release to Stop"
            if self.manual_btn and self.manual_btn.isVisible():
                self.manual_btn.setText("Release to Stop")

            # Call callback function
            if self.button_press_callback:
                self.button_press_callback()
        except Exception as e:
            self.logger.error(f"Failed to execute button press callback: {e}")

    def _on_manual_button_release(self):
        """Handle manual mode button release event"""
        try:
            # Update button text to "Press and Speak"
            if self.manual_btn and self.manual_btn.isVisible():
                self.manual_btn.setText("Press and Speak")

            # Call callback function
            if self.button_release_callback:
                self.button_release_callback()
        except Exception as e:
            self.logger.error(f"Failed to execute button release callback: {e}")

    def _on_auto_button_click(self):
        """Handle automatic mode button click event"""
        try:
            if self.auto_callback:
                self.auto_callback()
        except Exception as e:
            self.logger.error(f"Failed to execute automatic mode button callback: {e}")

    def _on_abort_button_click(self):
        """Handle abort button click event"""
        if self.abort_callback:
            self.abort_callback()

    def _on_mode_button_click(self):
        """Handle dialogue mode switch button click event"""
        try:
            # Check if mode can be switched (by asking application current state)
            if self.mode_callback:
                # If callback function returns False, current mode cannot be switched
                if not self.mode_callback(not self.auto_mode):
                    return

            # Switch mode
            self.auto_mode = not self.auto_mode

            # Update button display
            if self.auto_mode:
                # Switch to automatic mode
                self.update_mode_button_status("Automatic Dialogue")

                # Hide manual button, show automatic button
                self.update_queue.put(self._switch_to_auto_mode)
            else:
                # Switch to manual mode
                self.update_mode_button_status("Manual Dialogue")

                # Hide automatic button, show manual button
                self.update_queue.put(self._switch_to_manual_mode)

        except Exception as e:
            self.logger.error(f"Failed to execute mode switch button callback: {e}")

    def _switch_to_auto_mode(self):
        """Update UI for switching to automatic mode"""
        if self.manual_btn and self.auto_btn:
            self.manual_btn.hide()
            self.auto_btn.show()

    def _switch_to_manual_mode(self):
        """Update UI for switching to manual mode"""
        if self.manual_btn and self.auto_btn:
            self.auto_btn.hide()
            self.manual_btn.show()

    def update_status(self, status: str):
        """Update status text (only update main status)"""
        full_status_text = f"Status: {status}"
        self.update_queue.put(lambda: self._safe_update_label(self.status_label, full_status_text))
        
        # Update system tray icon
        if status != self.current_status:
            self.current_status = status
            self.update_queue.put(lambda: self._update_tray_icon(status))
        
        # Update microphone visualization based on status
        if "Listening" in status:
            self.update_queue.put(self._start_mic_visualization)
        elif "Standby" in status or "Speaking" in status:
            self.update_queue.put(self._stop_mic_visualization)

    def update_text(self, text: str):
        """Update TTS text"""
        self.update_queue.put(lambda: self._safe_update_label(self.tts_text_label, text))

    def update_emotion(self, emotion_path: str):
        """Update emotion animation"""
        # If path is the same, don't repeat setting emotion
        if hasattr(self, '_last_emotion_path') and self._last_emotion_path == emotion_path:
            return
            
        # Record current path
        self._last_emotion_path = emotion_path
        
        # Ensure UI update is handled in main thread
        if QApplication.instance().thread() != QThread.currentThread():
            # If not in main thread, use signal-slot mechanism or QMetaObject call in main thread
            QMetaObject.invokeMethod(self, "_update_emotion_safely",
                                    Qt.QueuedConnection,
                                    Q_ARG(str, emotion_path))
        else:
            # Already in main thread, execute directly
            self._update_emotion_safely(emotion_path)

    # New slot function, used to safely update emotion in main thread
    @pyqtSlot(str)
    def _update_emotion_safely(self, emotion_path: str):
        """Safely update emotion in main thread, avoiding thread issues"""
        if self.emotion_label:
            self.logger.info(f"Setting emotion GIF: {emotion_path}")
            try:
                self._set_emotion_gif(self.emotion_label, emotion_path)
            except Exception as e:
                self.logger.error(f"Error occurred while setting emotion GIF: {str(e)}")

    def _set_emotion_gif(self, label, gif_path):
        """Set emotion GIF animation, with fade effect"""
        # Basic check
        if not label or self.root.isHidden():
            return
            
        # Check if GIF is already displayed on label
        if hasattr(label, 'current_gif_path') and label.current_gif_path == gif_path:
            return
            
        # Record current GIF path to label object
        label.current_gif_path = gif_path

        try:
            # If current animation with same path is already playing, don't repeat setting
            if (self.emotion_movie and 
                getattr(self.emotion_movie, '_gif_path', None) == gif_path and
                self.emotion_movie.state() == QMovie.Running):
                return
                
            # If animation is in progress, only record next emotion to display, wait for current animation to finish
            if self.is_emotion_animating:
                self.next_emotion_path = gif_path
                return
                
            # Mark animation in progress
            self.is_emotion_animating = True
            
            # If previous animation is playing, fade out first
            if self.emotion_movie and label.movie() == self.emotion_movie:
                # Create opacity effect (if not already created)
                if not self.emotion_effect:
                    self.emotion_effect = QGraphicsOpacityEffect(label)
                    label.setGraphicsEffect(self.emotion_effect)
                    self.emotion_effect.setOpacity(1.0)
                
                # Create fade out animation
                self.emotion_animation = QPropertyAnimation(self.emotion_effect, b"opacity")
                self.emotion_animation.setDuration(180)  # Set animation duration (milliseconds)
                self.emotion_animation.setStartValue(1.0)
                self.emotion_animation.setEndValue(0.25)
                
                # After fade out, set new GIF and start fade in
                def on_fade_out_finished():
                    try:
                        # Stop current GIF
                        if self.emotion_movie:
                            self.emotion_movie.stop()
                        
                        # Set new GIF and start fade in
                        self._set_new_emotion_gif(label, gif_path)
                    except Exception as e:
                        self.logger.error(f"Failed to set GIF after fade out: {e}")
                        self.is_emotion_animating = False
                
                # Connect signal for fade out completion
                self.emotion_animation.finished.connect(on_fade_out_finished)
                
                # Start fade out animation
                self.emotion_animation.start()
            else:
                # If no previous animation, set new GIF and start fade in directly
                self._set_new_emotion_gif(label, gif_path)
                
        except Exception as e:
            self.logger.error(f"Failed to update emotion GIF animation: {e}")
            # If GIF load fails, try to display default emotion
            try:
                label.setText("😊")
            except Exception:
                pass
            self.is_emotion_animating = False
    
    def _set_new_emotion_gif(self, label, gif_path):
        """Set new GIF animation and execute fade in effect"""
        try:
            # Maintain GIF cache
            if not hasattr(self, '_gif_cache'):
                self._gif_cache = {}
                
            # Check if GIF is in cache
            if gif_path in self._gif_cache:
                movie = self._gif_cache[gif_path]
            else:
                # Log only when loaded for the first time
                self.logger.info(f"Loading GIF file: {gif_path}")
                # Create animation object
                movie = QMovie(gif_path)
                if not movie.isValid():
                    self.logger.error(f"Invalid GIF file: {gif_path}")
                    label.setText("😊")
                    self.is_emotion_animating = False
                    return
                
                # Configure animation and add to cache
                movie.setCacheMode(QMovie.CacheAll)
                self._gif_cache[gif_path] = movie
            
            # Save GIF path to movie object for comparison
            movie._gif_path = gif_path
            
            # Connect signal
            movie.error.connect(lambda: self.logger.error(f"GIF playback error: {movie.lastError()}"))
            
            # Save new animation object
            self.emotion_movie = movie
            
            # Set label size policy
            label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            label.setAlignment(Qt.AlignCenter)
            
            # Set animation to label
            label.setMovie(movie)
            
            # Set QMovie speed to 110, making animation smoother (default is 100)
            movie.setSpeed(105)
            
            # Ensure opacity is 0 (completely transparent)
            if self.emotion_effect:
                self.emotion_effect.setOpacity(0.0)
            else:
                self.emotion_effect = QGraphicsOpacityEffect(label)
                label.setGraphicsEffect(self.emotion_effect)
                self.emotion_effect.setOpacity(0.0)
            
            # Start playing animation
            movie.start()
            
            # Create fade in animation
            self.emotion_animation = QPropertyAnimation(self.emotion_effect, b"opacity")
            self.emotion_animation.setDuration(180)  # Fade in duration (milliseconds)
            self.emotion_animation.setStartValue(0.25)
            self.emotion_animation.setEndValue(1.0)
            
            # Check if there's next emotion to display after fade in completes
            def on_fade_in_finished():
                self.is_emotion_animating = False
                # If there's next emotion to display, continue switching
                if self.next_emotion_path:
                    next_path = self.next_emotion_path
                    self.next_emotion_path = None
                    self._set_emotion_gif(label, next_path)
            
            # 连接淡入完成信号
            self.emotion_animation.finished.connect(on_fade_in_finished)
            
            # 开始淡入动画
            self.emotion_animation.start()
            
        except Exception as e:
            self.logger.error(f"设置新的GIF动画失败: {e}")
            self.is_emotion_animating = False
            # 如果设置失败，尝试显示默认表情
            try:
                label.setText("😊")
            except Exception:
                pass

    def _safe_update_label(self, label, text):
        """Safely update label text"""
        if label and not self.root.isHidden():
            label.setText(text)

    def start_update_threads(self):
        """Start update threads"""
        def update_loop():
            while self._running:
                try:
                    # Process updates in queue
                    self._process_updates()
                    time.sleep(0.1)
                except Exception as e:
                    self.logger.error(f"Error in update thread: {e}")
                    time.sleep(1)

        # Start update thread
        update_thread = threading.Thread(target=update_loop)
        update_thread.daemon = True
        update_thread.start()

    def on_close(self):
        """Handle application close"""
        self._running = False
        self.stop_keyboard_listener()
        
        # Stop all timers
        if self.update_timer:
            self.update_timer.stop()
        if self.volume_update_timer:
            self.volume_update_timer.stop()
        if self.mic_timer:
            self.mic_timer.stop()
        if self.ha_update_timer:
            self.ha_update_timer.stop()
            
        # Close system tray
        if self.tray_icon:
            self.tray_icon.hide()

    def start(self):
        """Start GUI display"""
        try:
            # Create application instance if not exists
            if not QApplication.instance():
                self.app = QApplication(sys.argv)
            else:
                self.app = QApplication.instance()

            # Load UI from file
            from PyQt5 import uic
            ui_file = os.path.join(os.path.dirname(__file__), "gui_display.ui")
            self.root = uic.loadUi(ui_file)
            
            # Initialize UI components
            self._init_ui_components()
            
            # Set up navigation
            self._setup_navigation()
            
            # Set up system tray
            self._setup_tray_icon()
            
            # Start keyboard listener
            self.start_keyboard_listener()
            
            # Start update threads
            self.start_update_threads()
            
            # Show main window
            self.root.show()
            
            # Start event loop
            return self.app.exec_()
            
        except Exception as e:
            self.logger.error(f"Failed to start GUI: {e}")
            return 1

    def _setup_tray_icon(self):
        """Set up system tray icon"""
        try:
            # Create system tray icon
            self.tray_icon = QSystemTrayIcon(self.root)
            
            # Set default icon
            icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.png")
            if os.path.exists(icon_path):
                self.tray_icon.setIcon(QIcon(icon_path))
            
            # Create tray menu
            self.tray_menu = QMenu()
            
            # Add menu actions
            show_action = QAction("Show", self.root)
            show_action.triggered.connect(self._show_main_window)
            self.tray_menu.addAction(show_action)
            
            quit_action = QAction("Quit", self.root)
            quit_action.triggered.connect(self._quit_application)
            self.tray_menu.addAction(quit_action)
            
            # Set tray menu
            self.tray_icon.setContextMenu(self.tray_menu)
            
            # Connect tray icon activation signal
            self.tray_icon.activated.connect(self._tray_icon_activated)
            
            # Show tray icon
            self.tray_icon.show()
            
        except Exception as e:
            self.logger.error(f"Failed to set up system tray: {e}")

    def _update_tray_icon(self, status):
        """Update system tray icon based on status"""
        if not self.tray_icon:
            return
            
        try:
            # Get status color
            color = self._get_status_color(status)
            
            # Create colored icon
            icon_path = os.path.join(os.path.dirname(__file__), "assets", "icon.png")
            if os.path.exists(icon_path):
                pixmap = QPixmap(icon_path)
                if not pixmap.isNull():
                    # Create painter
                    painter = QPainter(pixmap)
                    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
                    painter.fillRect(pixmap.rect(), color)
                    painter.end()
                    
                    # Set new icon
                    self.tray_icon.setIcon(QIcon(pixmap))
                    
                    # Update tooltip
                    self.tray_icon.setToolTip(f"Xiaozhi AI - {status}")
                    
        except Exception as e:
            self.logger.error(f"Failed to update tray icon: {e}")

    def _get_status_color(self, status):
        """Get color based on status"""
        if "Connected" in status:
            return QColor(0, 255, 0)  # Green
        elif "Connecting" in status:
            return QColor(255, 165, 0)  # Orange
        elif "Disconnected" in status:
            return QColor(255, 0, 0)  # Red
        else:
            return QColor(128, 128, 128)  # Gray

    def _tray_icon_activated(self, reason):
        """Handle tray icon activation"""
        if reason == QSystemTrayIcon.DoubleClick:
            self._show_main_window()

    def _show_main_window(self):
        """Show main window"""
        if self.root:
            self.root.show()
            self.root.activateWindow()
            self.root.raise_()

    def _quit_application(self):
        """Quit application"""
        try:
            # Stop all timers and listeners
            self.on_close()
            
            # Close main window
            if self.root:
                self.root.close()
            
            # Quit application
            if self.app:
                self.app.quit()
                
        except Exception as e:
            self.logger.error(f"Failed to quit application: {e}")
            sys.exit(1)

    def _closeEvent(self, event):
        """Handle window close event"""
        try:
            # If system tray is enabled, minimize to tray instead of closing
            if self.tray_icon and self.tray_icon.isVisible():
                self.root.hide()
                event.ignore()
            else:
                # Otherwise, quit application
                self._quit_application()
                event.accept()
                
        except Exception as e:
            self.logger.error(f"Error in close event: {e}")
            event.accept()

    def update_mode_button_status(self, text: str):
        """Update mode button status"""
        self.update_queue.put(lambda: self._safe_update_button(self.mode_btn, text))

    def update_button_status(self, text: str):
        """Update button status"""
        if self.manual_btn and self.manual_btn.isVisible():
            self.update_queue.put(lambda: self._safe_update_button(self.manual_btn, text))
        elif self.auto_btn and self.auto_btn.isVisible():
            self.update_queue.put(lambda: self._safe_update_button(self.auto_btn, text))

    def _safe_update_button(self, button, text):
        """Safely update button text"""
        if button and not self.root.isHidden():
            button.setText(text)

    def _on_volume_change(self, value):
        """Handle volume slider change"""
        def update_volume():
            try:
                # Update volume label
                if self.volume_label:
                    self.volume_label.setText(f"{value}%")
                
                # Update system volume
                self.update_volume(value)
                
            except Exception as e:
                self.logger.error(f"Failed to update volume: {e}")
        
        # Add to update queue
        self.update_queue.put(update_volume)

    def update_volume(self, volume: int):
        """Update system volume"""
        try:
            # Update current volume
            self.current_volume = volume
            
            # Update volume controller if available
            if self.volume_control_available:
                if hasattr(self, 'volume_controller'):
                    self.volume_controller.set_volume(volume)
                    
            # Update volume label if available
            if self.volume_label:
                self.volume_label.setText(f"{volume}%")
                
        except Exception as e:
            self.logger.error(f"Failed to update system volume: {e}")
            self.volume_controller_failed = True

    def is_combo(self, *keys):
        """Check if a group of keys are pressed simultaneously"""
        return all(k in self.pressed_keys for k in keys)

    def start_keyboard_listener(self):
        """Start keyboard listener"""
        try:
            def on_press(key):
                try:
                    # Record pressed key
                    if (key == pynput_keyboard.Key.alt_l or 
                            key == pynput_keyboard.Key.alt_r):
                        self.pressed_keys.add('alt')
                    elif (key == pynput_keyboard.Key.shift_l or 
                          key == pynput_keyboard.Key.shift_r):
                        self.pressed_keys.add('shift')
                    elif hasattr(key, 'char') and key.char:
                        self.pressed_keys.add(key.char.lower())
                    
                    # Auto dialogue mode - Alt+Shift+A
                    if (self.is_combo('alt', 'shift', 'a') and 
                            self.auto_callback):
                        self.auto_callback()
                    
                    # Abort dialogue - Alt+Shift+X
                    if (self.is_combo('alt', 'shift', 'x') and 
                            self.abort_callback):
                        self.abort_callback()
                        
                except Exception as e:
                    self.logger.error(f"Keyboard event handling error: {e}")
            
            def on_release(key):
                try:
                    # Clear released key
                    if (key == pynput_keyboard.Key.alt_l or 
                            key == pynput_keyboard.Key.alt_r):
                        self.pressed_keys.discard('alt')
                    elif (key == pynput_keyboard.Key.shift_l or 
                          key == pynput_keyboard.Key.shift_r):
                        self.pressed_keys.discard('shift')
                    elif hasattr(key, 'char') and key.char:
                        self.pressed_keys.discard(key.char.lower())
                except Exception as e:
                    self.logger.error(f"Keyboard event handling error: {e}")

            # Create and start listener
            self.keyboard_listener = pynput_keyboard.Listener(
                on_press=on_press,
                on_release=on_release
            )
            self.keyboard_listener.start()
            self.logger.info("Keyboard listener initialized successfully")
        except Exception as e:
            self.logger.error(f"Keyboard listener initialization failed: {e}")

    def stop_keyboard_listener(self):
        """Stop keyboard listener"""
        if self.keyboard_listener:
            try:
                self.keyboard_listener.stop()
                self.keyboard_listener = None
                self.logger.info("Keyboard listener stopped")
            except Exception as e:
                self.logger.error(f"Failed to stop keyboard listener: {e}")

    def mousePressEvent(self, event: QMouseEvent):
        """Handle mouse press event"""
        if event.button() == Qt.LeftButton:
            self.last_mouse_pos = event.pos()

    def mouseReleaseEvent(self, event: QMouseEvent):
        """Handle mouse release event"""
        if event.button() == Qt.LeftButton and self.last_mouse_pos:
            # Calculate movement distance
            delta = event.pos() - self.last_mouse_pos
            distance = (delta.x() ** 2 + delta.y() ** 2) ** 0.5
            
            # If movement is small, treat as click
            if distance < 5:
                # Handle click event
                pass
                
            self.last_mouse_pos = None

    def _on_mute_click(self):
        """Handle mute button click"""
        try:
            if not self.is_muted:
                # Save current volume and mute
                self.pre_mute_volume = self.current_volume
                self.update_volume(0)
                self.is_muted = True
                if self.mute:
                    self.mute.setText("Unmute")
            else:
                # Restore previous volume
                self.update_volume(self.pre_mute_volume)
                self.is_muted = False
                if self.mute:
                    self.mute.setText("Mute")
                    
        except Exception as e:
            self.logger.error(f"Failed to handle mute: {e}")

    def _load_settings(self):
        """Load settings from configuration file"""
        try:
            if not os.path.exists(CONFIG_PATH):
                self.logger.warning("Configuration file not found")
                return
                
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
                
            # Load wake word settings
            if self.wakeWordEnableSwitch:
                self.wakeWordEnableSwitch.setChecked(
                    config.get('wake_word_enabled', False))
            if self.wakeWordsLineEdit:
                self.wakeWordsLineEdit.setText(
                    config.get('wake_words', ''))
                
            # Load network settings
            if self.deviceIdLineEdit:
                self.deviceIdLineEdit.setText(
                    config.get('device_id', ''))
            if self.wsProtocolComboBox:
                self.wsProtocolComboBox.setCurrentText(
                    config.get('ws_protocol', 'ws'))
            if self.wsAddressLineEdit:
                self.wsAddressLineEdit.setText(
                    config.get('ws_address', ''))
            if self.wsTokenLineEdit:
                self.wsTokenLineEdit.setText(
                    config.get('ws_token', ''))
                
            # Load OTA settings
            if self.otaProtocolComboBox:
                self.otaProtocolComboBox.setCurrentText(
                    config.get('ota_protocol', 'http'))
            if self.otaAddressLineEdit:
                self.otaAddressLineEdit.setText(
                    config.get('ota_address', ''))
                
            # Load Home Assistant settings
            if self.haProtocolComboBox:
                self.haProtocolComboBox.setCurrentText(
                    config.get('ha_protocol', 'http'))
            if hasattr(self, 'ha_server'):
                self.ha_server.setText(
                    config.get('ha_server', ''))
            if hasattr(self, 'ha_port'):
                self.ha_port.setText(
                    config.get('ha_port', ''))
            if hasattr(self, 'ha_key'):
                self.ha_key.setText(
                    config.get('ha_key', ''))
                    
        except Exception as e:
            self.logger.error(f"Failed to load settings: {e}")

    def _save_settings(self):
        """Save settings to configuration file"""
        try:
            # Create config directory if not exists
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            
            # Get current settings
            config = {
                'wake_word_enabled': self.wakeWordEnableSwitch.isChecked() if self.wakeWordEnableSwitch else False,
                'wake_words': self.wakeWordsLineEdit.text() if self.wakeWordsLineEdit else '',
                
                'device_id': self.deviceIdLineEdit.text() if self.deviceIdLineEdit else '',
                'ws_protocol': self.wsProtocolComboBox.currentText() if self.wsProtocolComboBox else 'ws',
                'ws_address': self.wsAddressLineEdit.text() if self.wsAddressLineEdit else '',
                'ws_token': self.wsTokenLineEdit.text() if self.wsTokenLineEdit else '',
                
                'ota_protocol': self.otaProtocolComboBox.currentText() if self.otaProtocolComboBox else 'http',
                'ota_address': self.otaAddressLineEdit.text() if self.otaAddressLineEdit else '',
                
                'ha_protocol': self.haProtocolComboBox.currentText() if self.haProtocolComboBox else 'http',
                'ha_server': self.ha_server.text() if hasattr(self, 'ha_server') else '',
                'ha_port': self.ha_port.text() if hasattr(self, 'ha_port') else '',
                'ha_key': self.ha_key.text() if hasattr(self, 'ha_key') else ''
            }
            
            # Save to file
            with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
                
            self.logger.info("Settings saved successfully")
            
            # Show success message
            QMessageBox.information(self.root, "Success", "Settings saved successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to save settings: {e}")
            QMessageBox.critical(self.root, "Error", f"Failed to save settings: {str(e)}")

    def _on_add_ha_devices_click(self):
        """Handle add Home Assistant devices button click"""
        try:
            # Get Home Assistant settings
            protocol = self.haProtocolComboBox.currentText()
            server = self.ha_server.text()
            port = self.ha_port.text()
            token = self.ha_key.text()
            
            if not all([protocol, server, port, token]):
                QMessageBox.warning(self.root, "Warning", "Please fill in all Home Assistant settings")
                return
                
            # Construct Home Assistant URL
            ha_url = f"{protocol}://{server}:{port}"
            
            # Show device selection dialog
            # TODO: Implement device selection dialog
            
        except Exception as e:
            self.logger.error(f"Failed to add Home Assistant devices: {e}")
            QMessageBox.critical(self.root, "Error", f"Failed to add devices: {str(e)}")

    def _update_mic_visualizer(self):
        """Update microphone visualizer"""
        try:
            if self.mic_visualizer and self.is_listening:
                # Get current microphone level
                level = self._get_current_mic_level()
                
                # Update visualizer
                self.mic_visualizer.set_volume(level)
                
        except Exception as e:
            self.logger.error(f"Failed to update microphone visualizer: {e}")

    def _get_current_mic_level(self):
        """Get current microphone level"""
        try:
            # TODO: Implement actual microphone level detection
            # For now, return a random value for testing
            return np.random.uniform(0, 1)
            
        except Exception as e:
            self.logger.error(f"Failed to get microphone level: {e}")
            return 0

    def _start_mic_visualization(self):
        """Start microphone visualization"""
        try:
            if not self.mic_timer:
                self.mic_timer = QTimer()
                self.mic_timer.timeout.connect(self._update_mic_visualizer)
                self.mic_timer.start(50)  # Update every 50ms
                
            self.is_listening = True
            
        except Exception as e:
            self.logger.error(f"Failed to start microphone visualization: {e}")

    def _stop_mic_visualization(self):
        """Stop microphone visualization"""
        try:
            if self.mic_timer:
                self.mic_timer.stop()
                
            if self.mic_visualizer:
                self.mic_visualizer.set_volume(0)
                
            self.is_listening = False
            
        except Exception as e:
            self.logger.error(f"Failed to stop microphone visualization: {e}")

    def _on_send_button_click(self):
        """Handle send button click"""
        try:
            # Get text from input field
            text = self.text_input.text().strip()
            if not text:
                return
                
            # Clear input field
            self.text_input.clear()
            
            # Send text through callback
            if self.send_text_callback:
                # Get application's event loop and run coroutine
                from src.application import Application
                app = Application.get_instance()
                if app and app.loop:
                    asyncio.run_coroutine_threadsafe(
                        self.send_text_callback(text),
                        app.loop
                    )
                else:
                    self.logger.error("Application instance or event loop not available")
                    
        except Exception as e:
            self.logger.error(f"Failed to send text: {e}")

    def _load_iot_devices(self):
        """Load IoT devices from Home Assistant"""
        try:
            # Get Home Assistant settings
            protocol = self.haProtocolComboBox.currentText()
            server = self.ha_server.text()
            port = self.ha_port.text()
            token = self.ha_key.text()
            
            if not all([protocol, server, port, token]):
                self.logger.warning("Home Assistant settings incomplete")
                return
                
            # Construct Home Assistant URL
            ha_url = f"{protocol}://{server}:{port}"
            
            # Clear existing devices
            self.devices_list.clear()
            self.device_labels.clear()
            self.device_states.clear()
            
            # TODO: Implement device loading from Home Assistant
            
            # Start device state update timer
            if not self.ha_update_timer:
                self.ha_update_timer = QTimer()
                self.ha_update_timer.timeout.connect(self._update_device_states)
                self.ha_update_timer.start(5000)  # Update every 5 seconds
                
        except Exception as e:
            self.logger.error(f"Failed to load IoT devices: {e}")

    def _update_device_states(self):
        """Update IoT device states"""
        try:
            # Get Home Assistant settings
            protocol = self.haProtocolComboBox.currentText()
            server = self.ha_server.text()
            port = self.ha_port.text()
            token = self.ha_key.text()
            
            if not all([protocol, server, port, token]):
                return
                
            # Construct Home Assistant URL
            ha_url = f"{protocol}://{server}:{port}"
            
            # Update each device state
            for entity_id, label in self.device_labels.items():
                self._fetch_device_state(ha_url, token, entity_id, label)
                
        except Exception as e:
            self.logger.error(f"Failed to update device states: {e}")

    def _fetch_device_state(self, ha_url, ha_token, entity_id, label):
        """Fetch device state from Home Assistant"""
        try:
            # TODO: Implement actual device state fetching
            # For now, return a random state for testing
            state = "on" if np.random.random() > 0.5 else "off"
            self._update_device_ui(entity_id, state, label)
            
        except Exception as e:
            self.logger.error(f"Failed to fetch device state: {e}")

    def _update_device_ui(self, entity_id, state, label):
        """Update device UI"""
        self.update_queue.put(
            lambda: self._safe_update_device_label(entity_id, state, label))

    def _safe_update_device_label(self, entity_id, state, label):
        """Safely update device label"""
        try:
            if label and not self.root.isHidden():
                # Update label text
                label.setText(f"{entity_id}: {state}")
                
                # Update state cache
                self.device_states[entity_id] = state
                
                # Update label color based on state
                if state == "on":
                    label.setStyleSheet("color: green;")
                else:
                    label.setStyleSheet("color: red;")
                    
        except Exception as e:
            self.logger.error(f"Failed to update device label: {e}")


class MicrophoneVisualizer(QFrame):
    """Microphone visualization widget"""
    
    def __init__(self, parent=None):
        """Initialize microphone visualizer"""
        super().__init__(parent)
        
        # Set widget properties
        self.setMinimumSize(100, 20)
        self.setMaximumHeight(20)
        
        # Initialize variables
        self.volume = 0
        self.animation_timer = QTimer()
        self.animation_timer.timeout.connect(self._update_animation)
        self.animation_timer.start(50)  # Update every 50ms
        
        # Set background color
        self.setStyleSheet("background-color: #2b2b2b;")
        
        # Initialize animation data
        self.wave_data = np.zeros(20)
        self.phase = 0

    def set_volume(self, volume):
        """Set current volume level"""
        try:
            # Clamp volume between 0 and 1
            self.volume = max(0, min(1, volume))
            
            # Update wave data
            self.wave_data = np.roll(self.wave_data, -1)
            self.wave_data[-1] = self.volume
            
        except Exception as e:
            logging.getLogger("Display").error(f"Failed to set volume: {e}")

    def _update_animation(self):
        """Update animation"""
        try:
            # Update phase
            self.phase = (self.phase + 0.1) % (2 * np.pi)
            
            # Force repaint
            self.update()
            
        except Exception as e:
            logging.getLogger("Display").error(f"Failed to update animation: {e}")

    def paintEvent(self, event):
        """Handle paint event"""
        try:
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            
            # Get widget rectangle
            rect = self.rect()
            
            # Draw waveform
            self._draw_waveform(painter, rect)
            
        except Exception as e:
            logging.getLogger("Display").error(f"Failed to paint: {e}")

    def _draw_waveform(self, painter, rect):
        """Draw waveform visualization"""
        try:
            # Set pen color
            painter.setPen(QPen(QColor(0, 255, 0), 2))
            
            # Calculate bar width and spacing
            bar_width = rect.width() / len(self.wave_data)
            spacing = bar_width * 0.2
            
            # Draw each bar
            for i, value in enumerate(self.wave_data):
                # Calculate bar height
                height = value * rect.height()
                
                # Calculate bar position
                x = i * bar_width + spacing
                y = (rect.height() - height) / 2
                
                # Draw bar
                painter.drawRect(QRect(x, y, bar_width - spacing, height))
                
        except Exception as e:
            logging.getLogger("Display").error(f"Failed to draw waveform: {e}")
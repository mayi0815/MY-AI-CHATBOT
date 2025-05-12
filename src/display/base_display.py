from abc import ABC, abstractmethod
from typing import Optional, Callable
import logging

class BaseDisplay(ABC):
    """Abstract base class for display interface"""

    def __init__(self):
        self.logger = logging.getLogger(self.__class__.__name__)
        self.current_volume = 70  # Default volume value
        self.volume_controller = None
        
        # Check volume control dependencies
        try:
            from src.utils.volume_controller import VolumeController
            if VolumeController.check_dependencies():
                self.volume_controller = VolumeController()
                self.logger.info("Volume controller initialized successfully")
                # Read current system volume
                try:
                    self.current_volume = self.volume_controller.get_volume()
                    self.logger.info(f"System volume read: {self.current_volume}%")
                except Exception as e:
                    self.logger.warning(f"Failed to get initial system volume: {e}, will use default value {self.current_volume}%")
            else:
                self.logger.warning("Volume control dependencies not met, will use default volume control")
        except Exception as e:
            self.logger.warning(f"Volume controller initialization failed: {e}, will use simulated volume control")

    @abstractmethod
    def set_callbacks(self,
                     press_callback: Optional[Callable] = None,
                     release_callback: Optional[Callable] = None,
                     status_callback: Optional[Callable] = None,
                     text_callback: Optional[Callable] = None,
                     emotion_callback: Optional[Callable] = None,
                     mode_callback: Optional[Callable] = None,
                     auto_callback: Optional[Callable] = None,
                     abort_callback: Optional[Callable] = None,
                     send_text_callback: Optional[Callable] = None):  # Add abort callback parameter
        """Set callback functions"""
        pass

    @abstractmethod
    def update_button_status(self, text: str):
        """Update button status"""
        pass

    @abstractmethod
    def update_status(self, status: str):
        """Update status text"""
        pass

    @abstractmethod
    def update_text(self, text: str):
        """Update TTS text"""
        pass

    @abstractmethod
    def update_emotion(self, emotion: str):
        """Update emotion"""
        pass

    def get_current_volume(self):
        """Get current volume"""
        if self.volume_controller:
            try:
                # Get latest volume from system
                self.current_volume = self.volume_controller.get_volume()
                # If successful, mark volume controller as working
                if hasattr(self, 'volume_controller_failed'):
                    self.volume_controller_failed = False
            except Exception as e:
                self.logger.debug(f"Failed to get system volume: {e}")
                # Mark volume controller as not working
                self.volume_controller_failed = True
        return self.current_volume

    def update_volume(self, volume: int):
        """Update system volume"""
        # Ensure volume is within valid range
        volume = max(0, min(100, volume))
        
        # Update internal volume value
        self.current_volume = volume
        self.logger.info(f"Setting volume: {volume}%")
        
        # Try to update system volume
        if self.volume_controller:
            try:
                self.volume_controller.set_volume(volume)
                self.logger.debug(f"System volume set to: {volume}%")
            except Exception as e:
                self.logger.warning(f"Failed to set system volume: {e}")

    @abstractmethod
    def start(self):
        """Start display"""
        pass

    @abstractmethod
    def on_close(self):
        """Close display"""
        pass

    @abstractmethod
    def start_keyboard_listener(self):
        """Start keyboard listener"""
        pass

    @abstractmethod
    def stop_keyboard_listener(self):
        """Stop keyboard listener"""
        pass
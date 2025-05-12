import asyncio
import threading
import time
import os
from typing import Optional, Callable

from src.display.base_display import BaseDisplay
# Replace keyboard import with pynput
from pynput import keyboard as pynput_keyboard

from src.utils.logging_config import get_logger


class CliDisplay(BaseDisplay):
    def __init__(self):
        super().__init__()  # Call parent class initialization
        """Initialize CLI display"""
        self.logger = get_logger(__name__)
        self.running = True

        # Status related
        self.current_status = "Not Connected"
        self.current_text = "Standby"
        self.current_emotion = "😊"
        self.current_volume = 0  # Add current volume attribute

        # Callback functions
        self.auto_callback = None
        self.status_callback = None
        self.text_callback = None
        self.emotion_callback = None
        self.abort_callback = None
        self.send_text_callback = None
        # Key state
        self.is_r_pressed = False
        # Add combo key support
        self.pressed_keys = set()

        # Status cache
        self.last_status = None
        self.last_text = None
        self.last_emotion = None
        self.last_volume = None

        # Keyboard listener
        self.keyboard_listener = None
        
        # Add event loop for async operations
        self.loop = asyncio.new_event_loop()

    def set_callbacks(self,
                      press_callback: Optional[Callable] = None,
                      release_callback: Optional[Callable] = None,
                      status_callback: Optional[Callable] = None,
                      text_callback: Optional[Callable] = None,
                      emotion_callback: Optional[Callable] = None,
                      mode_callback: Optional[Callable] = None,
                      auto_callback: Optional[Callable] = None,
                      abort_callback: Optional[Callable] = None,
                      send_text_callback: Optional[Callable] = None):
        """Set callback functions"""
        self.status_callback = status_callback
        self.text_callback = text_callback
        self.emotion_callback = emotion_callback
        self.auto_callback = auto_callback
        self.abort_callback = abort_callback
        self.send_text_callback = send_text_callback

    def update_button_status(self, text: str):
        """Update button status"""
        print(f"Button status: {text}")

    def update_status(self, status: str):
        """Update status text"""
        if status != self.current_status:
            self.current_status = status
            self._print_current_status()

    def update_text(self, text: str):
        """Update TTS text"""
        if text != self.current_text:
            self.current_text = text
            self._print_current_status()

    def update_emotion(self, emotion_path: str):
        """Update emotion
        emotion_path: GIF file path or emotion string
        """
        if emotion_path != self.current_emotion:
            # If it's a gif file path, extract filename as emotion name
            if emotion_path.endswith(".gif"):
                # Extract filename from path, remove .gif extension
                emotion_name = os.path.basename(emotion_path)
                emotion_name = emotion_name.replace(".gif", "")
                self.current_emotion = f"[{emotion_name}]"
            else:
                # If not a gif path, use directly
                self.current_emotion = emotion_path
            
            self._print_current_status()

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

    def start(self):
        """Start CLI display"""
        self._print_help()

        # Start status update thread
        self.start_update_threads()

        # Start keyboard listener thread
        keyboard_thread = threading.Thread(target=self._keyboard_listener)
        keyboard_thread.daemon = True
        keyboard_thread.start()

        # Start keyboard listener
        self.start_keyboard_listener()

        # Main loop
        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            self.on_close()

    def on_close(self):
        """Close CLI display"""
        self.running = False
        print("\nClosing application...")
        self.stop_keyboard_listener()

    def _print_help(self):
        """Print help information"""
        print("\n=== Xiaozhi AI Command Line Control ===")
        print("Available commands:")
        print("  r     - Start/Stop dialogue")
        print("  x     - Abort current dialogue")
        print("  s     - Show current status")
        print("  v num - Set volume (0-100)")
        print("  q     - Quit program")
        print("  h     - Show this help message")
        print("Shortcuts:")
        print("  Alt+Shift+A - Auto dialogue mode")
        print("  Alt+Shift+X - Abort current dialogue")
        print("=====================\n")

    def _keyboard_listener(self):
        """Keyboard listener thread"""
        try:
            while self.running:
                cmd = input().lower().strip()
                if cmd == 'q':
                    self.on_close()
                    break
                elif cmd == 'h':
                    self._print_help()
                elif cmd == 'r':
                    if self.auto_callback:
                        self.auto_callback()
                elif cmd == 'x':
                    if self.abort_callback:
                        self.abort_callback()
                elif cmd == 's':
                    self._print_current_status()
                elif cmd.startswith('v '):  # Add volume command handling
                    try:
                        volume = int(cmd.split()[1])  # Get volume value
                        if 0 <= volume <= 100:
                            self.update_volume(volume)
                            print(f"Volume set to: {volume}%")
                        else:
                            print("Volume must be between 0-100")
                    except (IndexError, ValueError):
                        print("Invalid volume value, format: v <0-100>")
                else:
                    if self.send_text_callback:
                        # Get application's event loop and run coroutine in it
                        from src.application import Application
                        app = Application.get_instance()
                        if app and app.loop:
                            asyncio.run_coroutine_threadsafe(
                                self.send_text_callback(cmd),
                                app.loop
                            )
                        else:
                            print("Application instance or event loop not available")
        except Exception as e:
            self.logger.error(f"Keyboard listener error: {e}")

    def start_update_threads(self):
        """Start status update threads"""
        def update_loop():
            while self.running:
                try:
                    # Check if status has changed
                    if (self.current_status != self.last_status or
                        self.current_text != self.last_text or
                        self.current_emotion != self.last_emotion or
                        self.current_volume != self.last_volume):
                        
                        self._print_current_status()
                        
                        # Update cache
                        self.last_status = self.current_status
                        self.last_text = self.current_text
                        self.last_emotion = self.current_emotion
                        self.last_volume = self.current_volume
                    
                    time.sleep(0.1)
                except Exception as e:
                    self.logger.error(f"Status update error: {e}")
                    time.sleep(1)

        # Start update thread
        update_thread = threading.Thread(target=update_loop)
        update_thread.daemon = True
        update_thread.start()

    def _print_current_status(self):
        """Print current status"""
        # Clear screen
        os.system('cls' if os.name == 'nt' else 'clear')
        
        # Print status
        print("\n=== Xiaozhi AI Status ===")
        print(f"Status: {self.current_status}")
        print(f"Text: {self.current_text}")
        print(f"Emotion: {self.current_emotion}")
        print(f"Volume: {self.current_volume}%")
        print("=======================\n")
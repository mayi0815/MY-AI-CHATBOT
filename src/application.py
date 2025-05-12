import asyncio
import json
import logging
import threading
import time
import sys
import traceback
from pathlib import Path

from src.utils.logging_config import get_logger
# Handle opus dynamic library before importing opuslib
from src.utils.opus_loader import setup_opus
from src.constants.constants import (
    DeviceState, EventType, AudioConfig,
    AbortReason, ListeningMode
)
from src.display import gui_display, cli_display
from src.utils.config_manager import ConfigManager
from src.utils.common_utils import handle_verification_code

setup_opus()

# Configure logging
logger = get_logger(__name__)

# Now import opuslib
try:
    import opuslib  # noqa: F401
    from src.utils.tts_utility import TtsUtility
except Exception as e:
    logger.critical("Failed to import opuslib: %s", e, exc_info=True)
    logger.critical("Please ensure opus dynamic library is correctly installed or in the correct location")
    sys.exit(1)

from src.protocols.mqtt_protocol import MqttProtocol
from src.protocols.websocket_protocol import WebsocketProtocol


class Application:
    _instance = None

    @classmethod
    def get_instance(cls):
        """Get singleton instance"""
        if cls._instance is None:
            logger.debug("Creating Application singleton instance")
            cls._instance = Application()
        return cls._instance

    def __init__(self):
        """Initialize application"""
        # Ensure singleton pattern
        if Application._instance is not None:
            logger.error("Attempting to create multiple instances of Application")
            raise Exception("Application is a singleton class, please use get_instance() to get the instance")
        Application._instance = self

        logger.debug("Initializing Application instance")
        # Get configuration manager instance
        self.config = ConfigManager.get_instance()
        self.config._initialize_mqtt_info()
        # State variables
        self.device_state = DeviceState.IDLE
        self.voice_detected = False
        self.keep_listening = False
        self.aborted = False
        self.current_text = ""
        self.current_emotion = "neutral"

        # Audio processing related
        self.audio_codec = None  # Will be initialized in _initialize_audio
        self._tts_lock = threading.Lock()
        self.is_tts_playing = False  # Since Display's playing state is only for GUI, not convenient for Music_player to use, added this flag to indicate TTS is speaking

        # Event loop and threads
        self.loop = asyncio.new_event_loop()
        self.loop_thread = None
        self.running = False
        self.input_event_thread = None
        self.output_event_thread = None

        # Task queue and lock
        self.main_tasks = []
        self.mutex = threading.Lock()

        # Protocol instance
        self.protocol = None

        # Callback functions
        self.on_state_changed_callbacks = []

        # Initialize event objects
        self.events = {
            EventType.SCHEDULE_EVENT: threading.Event(),
            EventType.AUDIO_INPUT_READY_EVENT: threading.Event(),
            EventType.AUDIO_OUTPUT_READY_EVENT: threading.Event()
        }

        # Create display interface
        self.display = None

        # Add wake word detector
        self.wake_word_detector = None
        logger.debug("Application instance initialization completed")

    def run(self, **kwargs):
        """Start application"""
        logger.info("Starting application with parameters: %s", kwargs)
        mode = kwargs.get('mode', 'gui')
        protocol = kwargs.get('protocol', 'websocket')

        # Start main loop thread
        logger.debug("Starting main loop thread")
        main_loop_thread = threading.Thread(target=self._main_loop)
        main_loop_thread.daemon = True
        main_loop_thread.start()

        # Initialize communication protocol
        logger.debug("Setting protocol type: %s", protocol)
        self.set_protocol_type(protocol)

        # Create and start event loop thread
        logger.debug("Starting event loop thread")
        self.loop_thread = threading.Thread(target=self._run_event_loop)
        self.loop_thread.daemon = True
        self.loop_thread.start()

        # Wait for event loop to be ready
        time.sleep(0.1)

        # Initialize application components (removed auto-connect)
        logger.debug("Initializing application components")
        asyncio.run_coroutine_threadsafe(
            self._initialize_without_connect(), 
            self.loop
        )

        # Initialize IoT devices
        self._initialize_iot_devices()

        logger.debug("Setting display type: %s", mode)
        self.set_display_type(mode)
        # Start GUI
        logger.debug("Starting display interface")
        self.display.start()

    def _run_event_loop(self):
        """Run event loop thread function"""
        logger.debug("Setting and starting event loop")
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def set_is_tts_playing(self, value: bool):
        with self._tts_lock:
            self.is_tts_playing = value

    def get_is_tts_playing(self) -> bool:
        with self._tts_lock:
            return self.is_tts_playing

    async def _initialize_without_connect(self):
        """Initialize application components (without establishing connection)"""
        logger.info("Initializing application components...")

        # Set device state to standby
        logger.debug("Setting initial device state to IDLE")
        self.schedule(lambda: self.set_device_state(DeviceState.IDLE))

        # Initialize audio codec
        logger.debug("Initializing audio codec")
        self._initialize_audio()

        # Initialize and start wake word detection
        self._initialize_wake_word_detector()
        
        # Set network protocol callback (MQTT AND WEBSOCKET)
        logger.debug("Setting protocol callback functions")
        self.protocol.on_network_error = self._on_network_error
        self.protocol.on_incoming_audio = self._on_incoming_audio
        self.protocol.on_incoming_json = self._on_incoming_json
        self.protocol.on_audio_channel_opened = self._on_audio_channel_opened
        self.protocol.on_audio_channel_closed = self._on_audio_channel_closed

        logger.info("Application components initialized successfully")

    def _initialize_audio(self):
        """Initialize audio device and codec"""
        try:
            logger.debug("Starting to initialize audio codec")
            from src.audio_codecs.audio_codec import AudioCodec
            self.audio_codec = AudioCodec()
            logger.info("Audio codec initialized successfully")

            # Record volume control status
            has_volume_control = (
                hasattr(self.display, 'volume_controller') and
                self.display.volume_controller
            )
            if has_volume_control:
                logger.info("System volume control enabled")
            else:
                logger.info("System volume control not enabled, using simulated volume control")

        except Exception as e:
            logger.error("Failed to initialize audio device: %s", e, exc_info=True)
            self.alert("Error", f"Failed to initialize audio device: {e}")

    def set_protocol_type(self, protocol_type: str):
        """Set protocol type"""
        logger.debug("Setting protocol type: %s", protocol_type)
        if protocol_type == 'mqtt':
            self.protocol = MqttProtocol(self.loop)
            logger.debug("Created MQTT protocol instance")
        else:  # websocket
            self.protocol = WebsocketProtocol()
            logger.debug("Created WebSocket protocol instance")

    def set_display_type(self, mode: str):
        """Initialize display interface"""
        logger.debug("Setting display interface type: %s", mode)
        # Manage different display modes through adapter concept
        if mode == 'gui':
            self.display = gui_display.GuiDisplay()
            logger.debug("Created GUI display interface")
            self.display.set_callbacks(
                press_callback=self.start_listening,
                release_callback=self.stop_listening,
                status_callback=self._get_status_text,
                text_callback=self._get_current_text,
                emotion_callback=self._get_current_emotion,
                mode_callback=self._on_mode_changed,
                auto_callback=self.toggle_chat_state,
                abort_callback=lambda: self.abort_speaking(
                    AbortReason.WAKE_WORD_DETECTED
                ),
                send_text_callback=self._send_text_tts
            )
        else:
            self.display = cli_display.CliDisplay()
            logger.debug("Created CLI display interface")
            self.display.set_callbacks(
                auto_callback=self.toggle_chat_state,
                abort_callback=lambda: self.abort_speaking(
                    AbortReason.WAKE_WORD_DETECTED
                ),
                status_callback=self._get_status_text,
                text_callback=self._get_current_text,
                emotion_callback=self._get_current_emotion,
                send_text_callback=self._send_text_tts
            )
        logger.debug("Display interface callback functions set successfully")

    def _main_loop(self):
        """Application main loop"""
        logger.info("Main loop started")
        self.running = True

        while self.running:
            # Wait for events
            for event_type, event in self.events.items():
                if event.is_set():
                    event.clear()
                    logger.debug("Processing event: %s", event_type)

                    if event_type == EventType.AUDIO_INPUT_READY_EVENT:
                        self._handle_input_audio()
                    elif event_type == EventType.AUDIO_OUTPUT_READY_EVENT:
                        self._handle_output_audio()
                    elif event_type == EventType.SCHEDULE_EVENT:
                        self._process_scheduled_tasks()

            # Short sleep to avoid high CPU usage
            time.sleep(0.01)

    def _process_scheduled_tasks(self):
        """Process scheduled tasks"""
        with self.mutex:
            tasks = self.main_tasks.copy()
            self.main_tasks.clear()

        logger.debug("Processing %d scheduled tasks", len(tasks))
        for task in tasks:
            try:
                task()
            except Exception as e:
                logger.error("Error executing scheduled task: %s", e, exc_info=True)

    def schedule(self, callback):
        """Schedule task to main loop"""
        with self.mutex:
            self.main_tasks.append(callback)
        self.events[EventType.SCHEDULE_EVENT].set()

    def _handle_input_audio(self):
        """Process audio input"""
        if self.device_state != DeviceState.LISTENING:
            return

        # Read and send audio data
        encoded_data = self.audio_codec.read_audio()
        if (encoded_data and self.protocol and
                self.protocol.is_audio_channel_opened()):
            asyncio.run_coroutine_threadsafe(
                self.protocol.send_audio(encoded_data),
                self.loop
            )

    async def _send_text_tts(self, text):
        """Convert text to speech and send"""
        try:
            tts_utility = TtsUtility(AudioConfig)

            # Generate Opus audio data packet
            opus_frames = await tts_utility.text_to_opus_audio(text)

            # Try to open audio channel
            if (not self.protocol.is_audio_channel_opened() and
                    DeviceState.IDLE == self.device_state):
                # Open audio channel
                success = await self.protocol.open_audio_channel()
                if not success:
                    logger.error("Failed to open audio channel")
                    return

            # Confirm opus frame generation success
            if opus_frames:
                logger.info(f"Generated {len(opus_frames)} Opus audio frames")

                # Set state to speaking
                self.schedule(lambda: self.set_device_state(DeviceState.SPEAKING))

                # Send audio data
                for i, frame in enumerate(opus_frames):
                    await self.protocol.send_audio(frame)
                    await asyncio.sleep(0.06)

                # Set chat message
                self.set_chat_message("user", text)
                await self.protocol.send_text(
                    json.dumps({"session_id": "", "type": "listen", "state": "stop"}))
                await self.protocol.send_text(b'')

                return True
            else:
                logger.error("Failed to generate audio")
                return False

        except Exception as e:
            logger.error(f"Error sending text to TTS: {e}")
            logger.error(traceback.format_exc())
            return False

    def _handle_output_audio(self):
        """Process audio output"""
        if self.device_state != DeviceState.SPEAKING:
            return
        self.set_is_tts_playing(True)   # Start playback
        self.audio_codec.play_audio()

    def _on_network_error(self, error_message=None):
        """Network error callback"""
        if error_message:
            logger.error(f"Network error: {error_message}")
            
        self.keep_listening = False
        self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
        # Resume wake word detection
        if self.wake_word_detector and self.wake_word_detector.paused:
            self.wake_word_detector.resume()

        if self.device_state != DeviceState.CONNECTING:
            logger.info("Detected connection loss")
            self.schedule(lambda: self.set_device_state(DeviceState.IDLE))

            # Close existing connection but don't close audio stream
            if self.protocol:
                asyncio.run_coroutine_threadsafe(
                    self.protocol.close_audio_channel(),
                    self.loop
                )

    def _on_incoming_audio(self, data):
        """Receive audio data callback"""
        if self.device_state == DeviceState.SPEAKING:
            self.audio_codec.write_audio(data)
            self.events[EventType.AUDIO_OUTPUT_READY_EVENT].set()

    def _on_incoming_json(self, json_data):
        """Receive JSON data callback"""
        try:
            if not json_data:
                return

            # Parse JSON data
            if isinstance(json_data, str):
                data = json.loads(json_data)
            else:
                data = json_data
            # Process different types of messages
            msg_type = data.get("type", "")
            if msg_type == "tts":
                self._handle_tts_message(data)
            elif msg_type == "stt":
                self._handle_stt_message(data)
            elif msg_type == "llm":
                self._handle_llm_message(data)
            elif msg_type == "iot":
                self._handle_iot_message(data)
            else:
                logger.warning(f"Received unknown type message: {msg_type}")
        except Exception as e:
            logger.error(f"Error processing JSON message: {e}")

    def _handle_tts_message(self, data):
        """Process TTS message"""
        state = data.get("state", "")
        if state == "start":
            self.schedule(lambda: self._handle_tts_start())
        elif state == "stop":
            self.schedule(lambda: self._handle_tts_stop())
        elif state == "sentence_start":
            text = data.get("text", "")
            if text:
                logger.info(f"<< {text}")
                self.schedule(lambda: self.set_chat_message("assistant", text))

                # Check if it contains verification code information
                import re
                match = re.search(r'((?:\d\s*){6,})', text)
                if match:
                    self.schedule(lambda: handle_verification_code(text))

    def _handle_tts_start(self):
        """Process TTS start event"""
        self.aborted = False
        self.set_is_tts_playing(True)   # Start playback
        # Clear possible existing old audio data
        self.audio_codec.clear_audio_queue()

        if self.device_state == DeviceState.IDLE or self.device_state == DeviceState.LISTENING:
            self.schedule(lambda: self.set_device_state(DeviceState.SPEAKING))

        # Commented out resume VAD detection code
        # if hasattr(self, 'vad_detector') and self.vad_detector:
        #     self.vad_detector.resume()

    def _handle_tts_stop(self):
        """Process TTS stop event"""
        if self.device_state == DeviceState.SPEAKING:
            # Give audio playback a buffer time to ensure all audio is played
            def delayed_state_change():
                # Wait until queue is empty or exceeds maximum retry attempts
                max_wait_attempts = 30  # Increase wait retry attempts
                wait_interval = 0.1  # Wait time interval each time
                attempts = 0

                # Wait until queue is empty or exceeds maximum retry attempts
                while (not self.audio_codec.audio_decode_queue.empty() and 
                       attempts < max_wait_attempts):
                    time.sleep(wait_interval)
                    attempts += 1

                # Ensure all data is played out
                # Wait extra time to ensure last data is processed
                if self.get_is_tts_playing():
                    time.sleep(0.5)

                # Set TTS playback state to False
                self.set_is_tts_playing(False)

                # State change
                if self.keep_listening:
                    asyncio.run_coroutine_threadsafe(
                        self.protocol.send_start_listening(ListeningMode.AUTO_STOP),
                        self.loop
                    )
                    self.schedule(lambda: self.set_device_state(DeviceState.LISTENING))
                else:
                    self.schedule(lambda: self.set_device_state(DeviceState.IDLE))

            # Schedule delayed execution
            # threading.Thread(target=delayed_state_change, daemon=True).start()
            self.schedule(delayed_state_change)

    def _handle_stt_message(self, data):
        """Process STT message"""
        text = data.get("text", "")
        if text:
            logger.info(f">> {text}")
            self.schedule(lambda: self.set_chat_message("user", text))

    def _handle_llm_message(self, data):
        """Process LLM message"""
        emotion = data.get("emotion", "")
        if emotion:
            self.schedule(lambda: self.set_emotion(emotion))

    async def _on_audio_channel_opened(self):
        """Audio channel opened callback"""
        logger.info("Audio channel opened")
        self.schedule(lambda: self._start_audio_streams())

        # Send IoT device descriptor
        from src.iot.thing_manager import ThingManager
        thing_manager = ThingManager.get_instance()
        asyncio.run_coroutine_threadsafe(
            self.protocol.send_iot_descriptors(thing_manager.get_descriptors_json()),
            self.loop
        )
        self._update_iot_states(False)


    def _start_audio_streams(self):
        """Start audio streams"""
        try:
            # Don't close and reopen streams, just ensure they're active
            if self.audio_codec.input_stream and not self.audio_codec.input_stream.is_active():
                try:
                    self.audio_codec.input_stream.start_stream()
                except Exception as e:
                    logger.warning(f"Error starting input stream: {e}")
                    # Only reinitialize if there's an error
                    self.audio_codec._reinitialize_input_stream()

            if self.audio_codec.output_stream and not self.audio_codec.output_stream.is_active():
                try:
                    self.audio_codec.output_stream.start_stream()
                except Exception as e:
                    logger.warning(f"Error starting output stream: {e}")
                    # Only reinitialize if there's an error
                    self.audio_codec._reinitialize_output_stream()

            # Set event trigger
            if self.input_event_thread is None or not self.input_event_thread.is_alive():
                self.input_event_thread = threading.Thread(
                    target=self._audio_input_event_trigger, daemon=True)
                self.input_event_thread.start()
                logger.info("Started input event trigger thread")

            # Check output event thread
            if self.output_event_thread is None or not self.output_event_thread.is_alive():
                self.output_event_thread = threading.Thread(
                    target=self._audio_output_event_trigger, daemon=True)
                self.output_event_thread.start()
                logger.info("Started output event trigger thread")

            logger.info("Audio streams started")
        except Exception as e:
            logger.error(f"Failed to start audio streams: {e}")

    def _audio_input_event_trigger(self):
        """Audio input event trigger"""
        while self.running:
            try:
                # Only trigger input events when actively listening
                if self.device_state == DeviceState.LISTENING and self.audio_codec.input_stream:
                    self.events[EventType.AUDIO_INPUT_READY_EVENT].set()
            except OSError as e:
                logger.error(f"Audio input stream error: {e}")
                # Don't exit loop, continue trying
                time.sleep(0.5)
            except Exception as e:
                logger.error(f"Audio input event trigger error: {e}")
                time.sleep(0.5)
            
            # Ensure trigger frequency is high enough even if frame length is large
            # Use 20ms as maximum trigger interval to ensure even if frame length is 60ms, enough sampling rate
            sleep_time = min(20, AudioConfig.FRAME_DURATION) / 1000
            time.sleep(sleep_time)  # Trigger by frame duration but ensure minimum trigger frequency

    def _audio_output_event_trigger(self):
        """Audio output event trigger"""
        while self.running:
            try:
                # Ensure output stream is active
                if (self.device_state == DeviceState.SPEAKING and
                    self.audio_codec and
                    self.audio_codec.output_stream):

                    # If output stream is not active, try to reactivate
                    if not self.audio_codec.output_stream.is_active():
                        try:
                            self.audio_codec.output_stream.start_stream()
                        except Exception as e:
                            logger.warning(f"Failed to start output stream, trying to reinitialize: {e}")
                            self.audio_codec._reinitialize_output_stream()

                    # Trigger event when queue has data
                    if not self.audio_codec.audio_decode_queue.empty():
                        self.events[EventType.AUDIO_OUTPUT_READY_EVENT].set()
            except Exception as e:
                logger.error(f"Audio output event trigger error: {e}")

            time.sleep(0.02)  # Slightly extend check interval

    async def _on_audio_channel_closed(self):
        """Audio channel closed callback"""
        logger.info("Audio channel closed")
        # Set to idle state but don't close audio stream
        self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
        self.keep_listening = False

        # Ensure wake word detection works normally
        if self.wake_word_detector:
            if not self.wake_word_detector.is_running():
                logger.info("Starting wake word detection in idle state")
                # Directly use AudioCodec instance instead of trying to get shared stream
                if hasattr(self, 'audio_codec') and self.audio_codec:
                    self.wake_word_detector.start(self.audio_codec)
                else:
                    self.wake_word_detector.start()
            elif self.wake_word_detector.paused:
                logger.info("Resuming wake word detection in idle state")
                self.wake_word_detector.resume()

    def set_device_state(self, state):
        """Set device state"""
        if self.device_state == state:
            return

        self.device_state = state

        # Execute corresponding operations based on state
        if state == DeviceState.IDLE:
            self.display.update_status("Standby")
            # self.display.update_emotion("😶")
            self.set_emotion("neutral")
            # Resume wake word detection (add safety check)
            if self.wake_word_detector and hasattr(self.wake_word_detector, 'paused') and self.wake_word_detector.paused:
                self.wake_word_detector.resume()
                logger.info("Wake word detection resumed")
            # Resume audio input stream
            if self.audio_codec and self.audio_codec.is_input_paused():
                self.audio_codec.resume_input()
        elif state == DeviceState.CONNECTING:
            self.display.update_status("Connecting...")
        elif state == DeviceState.LISTENING:
            self.display.update_status("Listening...")
            self.set_emotion("neutral")
            self._update_iot_states(True)
            # Pause wake word detection (add safety check)
            if self.wake_word_detector and hasattr(self.wake_word_detector, 'is_running') and self.wake_word_detector.is_running():
                self.wake_word_detector.pause()
                logger.info("Wake word detection paused")
            # Ensure audio input stream is active
            if self.audio_codec:
                if self.audio_codec.is_input_paused():
                    self.audio_codec.resume_input()
        elif state == DeviceState.SPEAKING:
            self.display.update_status("Speaking...")
            if self.wake_word_detector and hasattr(self.wake_word_detector, 'paused') and self.wake_word_detector.paused:
                self.wake_word_detector.resume()
            # Pause wake word detection (add safety check)
            # if self.wake_word_detector and hasattr(self.wake_word_detector, 'is_running') and self.wake_word_detector.is_running():
                # self.wake_word_detector.pause()
                # logger.info("Wake word detection paused")
            # Pause audio input stream to avoid self-listening
            # if self.audio_codec and not self.audio_codec.is_input_paused():
            #     self.audio_codec.pause_input()

        # Notify state change
        for callback in self.on_state_changed_callbacks:
            try:
                callback(state)
            except Exception as e:
                logger.error(f"Error executing state change callback: {e}")

    def _get_status_text(self):
        """Get current status text"""
        states = {
            DeviceState.IDLE: "Standby",
            DeviceState.CONNECTING: "Connecting...",
            DeviceState.LISTENING: "Listening...",
            DeviceState.SPEAKING: "Speaking..."
        }
        return states.get(self.device_state, "Unknown")

    def _get_current_text(self):
        """Get current display text"""
        return self.current_text

    def _get_current_emotion(self):
        """Get current emotion"""
        # If emotion hasn't changed, directly return cached path
        if hasattr(self, '_last_emotion') and self._last_emotion == self.current_emotion:
            return self._last_emotion_path
        
        # Get base path
        if getattr(sys, 'frozen', False):
            # Packaged environment
            if hasattr(sys, '_MEIPASS'):
                base_path = Path(sys._MEIPASS)
            else:
                base_path = Path(sys.executable).parent
        else:
            # Development environment
            base_path = Path(__file__).parent.parent
            
        emotion_dir = base_path / "assets" / "emojis"
            
        emotions = {
            "neutral": str(emotion_dir / "neutral.gif"),
            "happy": str(emotion_dir / "happy.gif"),
            "laughing": str(emotion_dir / "laughing.gif"),
            "funny": str(emotion_dir / "funny.gif"),
            "sad": str(emotion_dir / "sad.gif"),
            "angry": str(emotion_dir / "angry.gif"),
            "crying": str(emotion_dir / "crying.gif"),
            "loving": str(emotion_dir / "loving.gif"),
            "embarrassed": str(emotion_dir / "embarrassed.gif"),
            "surprised": str(emotion_dir / "surprised.gif"),
            "shocked": str(emotion_dir / "shocked.gif"),
            "thinking": str(emotion_dir / "thinking.gif"),
            "winking": str(emotion_dir / "winking.gif"),
            "cool": str(emotion_dir / "cool.gif"),
            "relaxed": str(emotion_dir / "relaxed.gif"),
            "delicious": str(emotion_dir / "delicious.gif"),
            "kissy": str(emotion_dir / "kissy.gif"),
            "confident": str(emotion_dir / "confident.gif"),
            "sleepy": str(emotion_dir / "sleepy.gif"),
            "silly": str(emotion_dir / "silly.gif"),
            "confused": str(emotion_dir / "confused.gif")
        }
        
        # Save current emotion and corresponding path
        self._last_emotion = self.current_emotion
        self._last_emotion_path = emotions.get(self.current_emotion, str(emotion_dir / "neutral.gif"))
        
        logger.debug(f"Emotion path: {self._last_emotion_path}")
        return self._last_emotion_path

    def set_chat_message(self, role, message):
        """Set chat message"""
        self.current_text = message
        # Update display
        if self.display:
            self.display.update_text(message)

    def set_emotion(self, emotion):
        """Set emotion"""
        self.current_emotion = emotion
        # Update display
        if self.display:
            self.display.update_emotion(self._get_current_emotion())

    def start_listening(self):
        """Start listening"""
        self.schedule(self._start_listening_impl)

    def _start_listening_impl(self):
        """Start listening implementation"""
        if not self.protocol:
            logger.error("Protocol not initialized")
            return

        self.keep_listening = False

        # Check if wake word detector exists
        if self.wake_word_detector:
            self.wake_word_detector.pause()

        if self.device_state == DeviceState.IDLE:
            self.schedule(lambda: self.set_device_state(DeviceState.CONNECTING))  # Set device state to connecting
            # Try to open audio channel
            if not self.protocol.is_audio_channel_opened():
                try:
                    # Wait for asynchronous operation to complete
                    future = asyncio.run_coroutine_threadsafe(
                        self.protocol.open_audio_channel(),
                        self.loop
                    )
                    # Wait for operation to complete and get result
                    success = future.result(timeout=10.0)  # Add timeout time

                    if not success:
                        self.alert("Error", "Failed to open audio channel")  # Pop up error prompt
                        self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
                        return

                except Exception as e:
                    logger.error(f"Error occurred when opening audio channel: {e}")
                    self.alert("Error", f"Failed to open audio channel: {str(e)}")
                    self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
                    return
                
            # --- Force reinitialize input stream --- 
            try:
                if self.audio_codec:
                     self.audio_codec._reinitialize_input_stream() # Call reinitialize
                else:
                     logger.warning("Cannot force reinitialization, audio_codec is None.")
            except Exception as force_reinit_e:
                logger.error(f"Forced reinitialization failed: {force_reinit_e}", exc_info=True)
                self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
                if self.wake_word_detector and self.wake_word_detector.paused:
                     self.wake_word_detector.resume()
                return
            # --- Force reinitialize end --- 

            asyncio.run_coroutine_threadsafe(
                self.protocol.send_start_listening(ListeningMode.MANUAL),
                self.loop
            )
            self.schedule(lambda: self.set_device_state(DeviceState.LISTENING))
        elif self.device_state == DeviceState.SPEAKING:
            if not self.aborted:
                self.abort_speaking(AbortReason.WAKE_WORD_DETECTED)

    async def _open_audio_channel_and_start_manual_listening(self):
        """Open audio channel and start manual listening"""
        if not await self.protocol.open_audio_channel():
            self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
            self.alert("Error", "Failed to open audio channel")
            return

        await self.protocol.send_start_listening(ListeningMode.MANUAL)
        self.schedule(lambda: self.set_device_state(DeviceState.LISTENING))

    def toggle_chat_state(self):
        """Toggle chat state"""
        # Check if wake word detector exists
        if self.wake_word_detector:
            self.wake_word_detector.pause()
        self.schedule(self._toggle_chat_state_impl)

    def _toggle_chat_state_impl(self):
        """Toggle chat state implementation"""
        # Check if protocol is initialized
        if not self.protocol:
            logger.error("Protocol not initialized")
            return

        # If device is currently idle, try to connect and start listening
        if self.device_state == DeviceState.IDLE:
            self.schedule(lambda: self.set_device_state(DeviceState.CONNECTING))  # Set device state to connecting
            # Use thread to handle connection operation to avoid blocking
            def connect_and_listen():
                # Try to open audio channel
                if not self.protocol.is_audio_channel_opened():
                    try:
                        # Wait for asynchronous operation to complete
                        future = asyncio.run_coroutine_threadsafe(
                            self.protocol.open_audio_channel(),
                            self.loop
                        )
                        # Wait for operation to complete and get result, use shorter timeout time
                        try:
                            success = future.result(timeout=5.0)
                        except asyncio.TimeoutError:
                            logger.error("Audio channel open timeout")
                            self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
                            self.alert("Error", "Audio channel open timeout")
                            return
                        except Exception as e:
                            logger.error(f"Unknown error occurred when opening audio channel: {e}")
                            self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
                            self.alert("Error", f"Failed to open audio channel: {str(e)}")
                            return

                        if not success:
                            self.alert("Error", "Failed to open audio channel")  # Pop up error prompt
                            self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
                            return

                    except Exception as e:
                        logger.error(f"Error occurred when opening audio channel: {e}")
                        self.alert("Error", f"Failed to open audio channel: {str(e)}")
                        self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
                        return

                self.keep_listening = True  # Start listening
                # Start automatic stop listening mode
                try:
                    asyncio.run_coroutine_threadsafe(
                        self.protocol.send_start_listening(ListeningMode.AUTO_STOP),
                        self.loop
                    )
                    self.schedule(lambda: self.set_device_state(DeviceState.LISTENING))
                except Exception as e:
                    logger.error(f"Error occurred when starting listening: {e}")
                    self.set_device_state(DeviceState.IDLE)
                    self.alert("Error", f"Failed to start listening: {str(e)}")

            # Start connection thread
            threading.Thread(target=connect_and_listen, daemon=True).start()

        # If device is speaking, stop current speaking
        elif self.device_state == DeviceState.SPEAKING:
            self.abort_speaking(AbortReason.NONE)  # Stop speaking

        # If device is listening, close audio channel
        elif self.device_state == DeviceState.LISTENING:
            # Use thread to handle close operation to avoid blocking
            def close_audio_channel():
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        self.protocol.close_audio_channel(),
                        self.loop
                    )
                    future.result(timeout=3.0)  # Use shorter timeout
                except Exception as e:
                    logger.error(f"Error occurred when closing audio channel: {e}")
            
            threading.Thread(target=close_audio_channel, daemon=True).start()
            # Immediately set to idle state, don't wait for close to complete
            self.schedule(lambda: self.set_device_state(DeviceState.IDLE))

    def stop_listening(self):
        """Stop listening"""
        self.schedule(self._stop_listening_impl)

    def _stop_listening_impl(self):
        """Stop listening implementation"""
        if self.device_state == DeviceState.LISTENING:
            asyncio.run_coroutine_threadsafe(
                self.protocol.send_stop_listening(),
                self.loop
            )
            self.set_device_state(DeviceState.IDLE)

    def abort_speaking(self, reason):
        """Stop speech output"""
        # If already aborted, don't repeat processing
        if self.aborted:
            logger.debug(f"Already aborted, ignoring repeated abort request: {reason}")
            return

        logger.info(f"Stop speech output, reason: {reason}")
        self.aborted = True

        # Set TTS playback state to False
        self.set_is_tts_playing(False)
        
        # Immediately clear audio queue
        if self.audio_codec:
            self.audio_codec.clear_audio_queue()

        # If it's because of wake word stopping speech, first pause wake word detector to avoid Vosk assertion error
        if reason == AbortReason.WAKE_WORD_DETECTED and self.wake_word_detector:
            if hasattr(self.wake_word_detector, 'is_running') and self.wake_word_detector.is_running():
                # Pause wake word detector
                self.wake_word_detector.pause()
                logger.debug("Temporarily pause wake word detector to avoid concurrent processing")
                # Brief wait to ensure wake word detector is paused processing
                time.sleep(0.1)

        # Use thread to handle state change and asynchronous operation to avoid blocking main thread
        def process_abort():
            # First send abort command
            try:
                future = asyncio.run_coroutine_threadsafe(
                    self.protocol.send_abort_speaking(reason),
                    self.loop
                )
                # Use shorter timeout to ensure not long blocking
                future.result(timeout=1.0)
            except Exception as e:
                logger.error(f"Error sending abort command: {e}")
            
            # Then set state
            # self.set_device_state(DeviceState.IDLE)
            self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
            # If it's because of wake word triggering abort and auto-listening is enabled, automatically enter recording mode
            if (reason == AbortReason.WAKE_WORD_DETECTED and 
                    self.keep_listening and 
                    self.protocol.is_audio_channel_opened()):
                # Brief delay to ensure abort command is processed
                time.sleep(0.1)  # Shorten delay time
                self.schedule(lambda: self.toggle_chat_state())
        
        # Start processing thread
        threading.Thread(target=process_abort, daemon=True).start()

    def alert(self, title, message):
        """Show warning information"""
        logger.warning(f"Warning: {title}, {message}")
        # Show warning on GUI
        if self.display:
            self.display.update_text(f"{title}: {message}")

    def on_state_changed(self, callback):
        """Register state change callback"""
        self.on_state_changed_callbacks.append(callback)

    def shutdown(self):
        """Close application"""
        logger.info("Closing application...")
        self.running = False

        # Close audio codec
        if self.audio_codec:
            self.audio_codec.close()

        # Close protocol
        if self.protocol:
            asyncio.run_coroutine_threadsafe(
                self.protocol.close_audio_channel(),
                self.loop
            )

        # Stop event loop
        if self.loop and self.loop.is_running():
            self.loop.call_soon_threadsafe(self.loop.stop)

        # Wait for event loop thread to end
        if self.loop_thread and self.loop_thread.is_alive():
            self.loop_thread.join(timeout=1.0)

        # Stop wake word detection
        if self.wake_word_detector:
            self.wake_word_detector.stop()

        # Close VAD detector
        # if hasattr(self, 'vad_detector') and self.vad_detector:
        #     self.vad_detector.stop()

        logger.info("Application closed")

    def _on_mode_changed(self, auto_mode):
        """Process dialog mode change"""
        # Only allow switching mode when IDLE
        if self.device_state != DeviceState.IDLE:
            self.alert("Warning", "Only standby state can switch dialog mode")
            return False

        self.keep_listening = auto_mode
        logger.info(f"Dialog mode switched to: {'Automatic' if auto_mode else 'Manual'}")
        return True

    def _initialize_wake_word_detector(self):
        """Initialize wake word detector"""
        # First check if wake word feature is enabled in configuration
        if not self.config.get_config('WAKE_WORD_OPTIONS.USE_WAKE_WORD', False):
            logger.info("Wake word feature is disabled in configuration, skipping initialization")
            self.wake_word_detector = None
            return

        try:
            from src.audio_processing.wake_word_detect import WakeWordDetector

            # Create detector instance
            self.wake_word_detector = WakeWordDetector()

            # If wake word detector is disabled (internal failure), update configuration
            if not getattr(self.wake_word_detector, 'enabled', True):
                logger.warning("Wake word detector is disabled (internal failure)")
                self.config.update_config("WAKE_WORD_OPTIONS.USE_WAKE_WORD", False)
                self.wake_word_detector = None
                return

            # Register wake word detection callback and error handling
            self.wake_word_detector.on_detected(self._on_wake_word_detected)
            
            # Use lambda to capture self, instead of defining separate function
            self.wake_word_detector.on_error = lambda error: (
                self._handle_wake_word_error(error)
            )
            
            logger.info("Wake word detector initialized successfully")

            # Start wake word detector
            self._start_wake_word_detector()

        except Exception as e:
            logger.error(f"Failed to initialize wake word detector: {e}")
            import traceback
            logger.error(traceback.format_exc())

            # Disable wake word feature, but don't affect other program functionality
            self.config.update_config("WAKE_WORD_OPTIONS.USE_WAKE_WORD", False)
            logger.info("Wake word feature disabled due to initialization failure, but program will continue running")
            self.wake_word_detector = None

    def _handle_wake_word_error(self, error):
        """Process wake word detector error"""
        logger.error(f"Wake word detection error: {error}")
        # Try to restart detector
        if self.device_state == DeviceState.IDLE:
            self.schedule(lambda: self._restart_wake_word_detector())

    def _start_wake_word_detector(self):
        """Start wake word detector"""
        if not self.wake_word_detector:
            return
        
        # Ensure audio codec is initialized
        if hasattr(self, 'audio_codec') and self.audio_codec:
            logger.info("Using audio codec to start wake word detector")
            self.wake_word_detector.start(self.audio_codec)
        else:
            # If no audio codec, use standalone mode
            logger.info("Using standalone mode to start wake word detector")
            self.wake_word_detector.start()

    def _on_wake_word_detected(self, wake_word, full_text):
        """Wake word detection callback"""
        logger.info(f"Detected wake word: {wake_word} (Full text: {full_text})")
        self.schedule(lambda: self._handle_wake_word_detected(wake_word))

    def _handle_wake_word_detected(self, wake_word):
        """Process wake word detection event"""
        if self.device_state == DeviceState.IDLE:
            # Pause wake word detection
            if self.wake_word_detector:
                self.wake_word_detector.pause()

            # Start connecting and listening
            self.schedule(lambda: self.set_device_state(DeviceState.CONNECTING))
            # Try connecting and opening audio channel
            asyncio.run_coroutine_threadsafe(
                self._connect_and_start_listening(wake_word),
                self.loop
            )
        elif self.device_state == DeviceState.SPEAKING:
            self.abort_speaking(AbortReason.WAKE_WORD_DETECTED)

    async def _connect_and_start_listening(self, wake_word):
        """Connect to server and start listening"""
        # First try to connect to server
        if not await self.protocol.connect():
            logger.error("Failed to connect to server")
            self.alert("Error", "Failed to connect to server")
            self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
            # Resume wake word detection
            if self.wake_word_detector:
                self.wake_word_detector.resume()
            return

        # Then try to open audio channel
        if not await self.protocol.open_audio_channel():
            logger.error("Failed to open audio channel")
            self.schedule(lambda: self.set_device_state(DeviceState.IDLE))
            self.alert("Error", "Failed to open audio channel")
            # Resume wake word detection
            if self.wake_word_detector:
                self.wake_word_detector.resume()
            return

        await self.protocol.send_wake_word_detected(wake_word)
        # Set to automatic listening mode
        self.keep_listening = True
        await self.protocol.send_start_listening(ListeningMode.AUTO_STOP)
        self.schedule(lambda: self.set_device_state(DeviceState.LISTENING))

    def _restart_wake_word_detector(self):
        """Restart wake word detector"""
        logger.info("Trying to restart wake word detector")
        try:
            # Stop existing detector
            if self.wake_word_detector:
                self.wake_word_detector.stop()
                time.sleep(0.5)  # Give some time for resources to release

            # Directly use audio codec
            if hasattr(self, 'audio_codec') and self.audio_codec:
                self.wake_word_detector.start(self.audio_codec)
                logger.info("Using audio codec to restart wake word detector")
            else:
                # If no audio codec, use standalone mode
                self.wake_word_detector.start()
                logger.info("Using standalone mode to restart wake word detector")

            logger.info("Wake word detector restarted successfully")
        except Exception as e:
            logger.error(f"Failed to restart wake word detector: {e}")

    def _initialize_iot_devices(self):
        """Initialize IoT devices"""
        from src.iot.thing_manager import ThingManager
        from src.iot.things.lamp import Lamp
        from src.iot.things.speaker import Speaker
        from src.iot.things.music_player import MusicPlayer
        from src.iot.things.CameraVL.Camera import Camera
        # from src.iot.things.query_bridge_rag import QueryBridgeRAG
        # from src.iot.things.temperature_sensor import TemperatureSensor
        # Import Home Assistant device control classes
        from src.iot.things.ha_control import HomeAssistantLight, HomeAssistantSwitch, HomeAssistantNumber, HomeAssistantButton
        # Import new countdown timer device
        from src.iot.things.countdown_timer import CountdownTimer
        
        # Get IoT device manager instance
        thing_manager = ThingManager.get_instance()

        # Add devices
        thing_manager.add_thing(Lamp())
        thing_manager.add_thing(Speaker())
        thing_manager.add_thing(MusicPlayer())
        # Disable following examples by default
        thing_manager.add_thing(Camera())
        # thing_manager.add_thing(QueryBridgeRAG())
        # thing_manager.add_thing(TemperatureSensor())

        # Add countdown timer device
        thing_manager.add_thing(CountdownTimer())
        logger.info("Added countdown timer device for timed command execution")

        # Add Home Assistant devices
        ha_devices = self.config.get_config("HOME_ASSISTANT.DEVICES", [])
        for device in ha_devices:
            entity_id = device.get("entity_id")
            friendly_name = device.get("friendly_name")
            if entity_id:
                # Determine device type based on entity ID
                if entity_id.startswith("light."):
                    # Light device
                    thing_manager.add_thing(HomeAssistantLight(entity_id, friendly_name))
                    logger.info(f"Added Home Assistant light device: {friendly_name or entity_id}")
                elif entity_id.startswith("switch."):
                    # Switch device
                    thing_manager.add_thing(HomeAssistantSwitch(entity_id, friendly_name))
                    logger.info(f"Added Home Assistant switch device: {friendly_name or entity_id}")
                elif entity_id.startswith("number."):
                    # Number device (e.g., volume control)
                    thing_manager.add_thing(HomeAssistantNumber(entity_id, friendly_name))
                    logger.info(f"Added Home Assistant number device: {friendly_name or entity_id}")
                elif entity_id.startswith("button."):
                    # Button device
                    thing_manager.add_thing(HomeAssistantButton(entity_id, friendly_name))
                    logger.info(f"Added Home Assistant button device: {friendly_name or entity_id}")
                else:
                    # Default to light device
                    thing_manager.add_thing(HomeAssistantLight(entity_id, friendly_name))
                    logger.info(f"Added Home Assistant device (default as light): {friendly_name or entity_id}")

        logger.info("IoT devices initialization completed")

    def _handle_iot_message(self, data):
        """Process IoT message"""
        from src.iot.thing_manager import ThingManager
        thing_manager = ThingManager.get_instance()

        commands = data.get("commands", [])
        for command in commands:
            try:
                result = thing_manager.invoke(command)
                logger.info(f"IoT command execution result: {result}")
                # self.schedule(lambda: self._update_iot_states())
            except Exception as e:
                logger.error(f"Failed to execute IoT command: {e}")

    def _update_iot_states(self, delta=None):
        """
        Update IoT device status

        Args:
            delta: Whether to send only changed parts
                   - None: Use original behavior, always send all states
                   - True: Send only changed parts
                   - False: Send all states and reset cache
        """
        from src.iot.thing_manager import ThingManager
        thing_manager = ThingManager.get_instance()

        # Handle downward compatibility
        if delta is None:
            # Keep original behavior: Get all states and send
            states_json = thing_manager.get_states_json_str()  # Call old method

            # Send status update
            asyncio.run_coroutine_threadsafe(
                self.protocol.send_iot_states(states_json),
                self.loop
            )
            logger.info("IoT device status updated")
            return

        # Use new method to get status
        changed, states_json = thing_manager.get_states_json(delta=delta)
        # delta=False always sends, delta=True only sends when changed
        if not delta or changed:
            asyncio.run_coroutine_threadsafe(
                self.protocol.send_iot_states(states_json),
                self.loop
            )
            if delta:
                logger.info("IoT device status updated (incremental)")
            else:
                logger.info("IoT device status updated (full)")
        else:
            logger.debug("IoT device status unchanged, skipping update")

    def _update_wake_word_detector_stream(self):
        """Update wake word detector audio stream"""
        if self.wake_word_detector and self.audio_codec and self.wake_word_detector.is_running():
            # Directly reference AudioCodec instance input stream
            if self.audio_codec.input_stream and self.audio_codec.input_stream.is_active():
                self.wake_word_detector.stream = self.audio_codec.input_stream
                self.wake_word_detector.external_stream = True
                logger.info("Updated wake word detector audio stream reference")

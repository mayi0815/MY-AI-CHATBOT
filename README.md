
## Project Introduction
py-xiaozhi is a Python-based Xiaozhi voice client, designed to learn coding and experience AI voice interaction without hardware requirements. This repository is ported from xiaozhi-esp32.

## Demo
![Image](./documents/docs/guide/images/系统界面.png)

## Features
- **AI Voice Interaction**: Supports voice input and recognition, enabling smart human-computer interaction with natural conversation flow.
- **Visual Multimodal**: Supports image recognition and processing, providing multimodal interaction capabilities and image content understanding.
- **IoT Device Integration**: 
  - Supports smart home device control including lights, volume, temperature sensors, etc.
  - Integrates with Home Assistant smart home platform to control lights, switches, number controllers, and buttons
  - Provides countdown timer functionality for delayed command execution
  - Features built-in virtual devices and physical device drivers, easily extensible
- **Online Music Playback**: Advanced Music Player: A high-performance music player built on Pygame, supporting play/pause/stop, progress control, lyric display, and local caching, delivering a more stable and smooth listening experience.
- **Voice Wake-up**: Supports wake word activation, eliminating manual operation (disabled by default, manual activation required).
- **Auto Dialogue Mode**: Implements continuous dialogue experience, enhancing user interaction fluidity.
- **Graphical Interface**: Provides intuitive GUI with Xiaozhi expressions and text display, enhancing visual experience.
- **Command Line Mode**: Supports CLI operation, suitable for embedded devices or environments without GUI.
- **Cross-platform Support**: Compatible with Windows 10+, macOS 10.15+, and Linux systems for use anywhere.
- **Volume Control**: Supports volume adjustment to adapt to different environmental requirements with unified sound control interface.
- **Session Management**: Effectively manages multi-turn dialogues to maintain interaction continuity.
- **Encrypted Audio Transmission**: Supports WSS protocol to ensure audio data security and prevent information leakage.
- **Automatic Verification Code Handling**: Automatically copies verification codes and opens browsers during first use, simplifying user operations.
- **Automatic MAC Address Acquisition**: Avoids MAC address conflicts and improves connection stability.
- **Modular Code**: Code is split and encapsulated into classes with clear responsibilities, facilitating secondary development.
- **Stability Optimization**: Fixes multiple issues including reconnection and cross-platform compatibility.

## System Requirements
- Python version: 3.9 >= version <= 3.12
- Supported operating systems: Windows 10+, macOS 10.15+, Linux
- Microphone and speaker devices

## Read This First!
- Carefully read the project documentation for startup tutorials and file descriptions
- The main branch has the latest code; manually reinstall pip dependencies after each update to ensure you have new dependencies

## Configuration System
The project uses a layered configuration system, including:

1. **Basic Configuration**: Sets basic runtime parameters, located in `config/config.json`
2. **Device Activation**: Device identity information, stored in `config/efuse.json`
3. **Wake Word Settings**: Voice wake-up related configuration
4. **IoT Devices**: Configuration for various IoT devices, including temperature sensors and Home Assistant integration

For detailed configuration instructions, please refer to the Configuration Documentation

## IoT Functionality
py-xiaozhi provides rich IoT device control features:

- **Virtual Devices**: Light control, volume adjustment, countdown timers, etc.
- **Physical Device Integration**: Temperature sensors, cameras, etc.
- **Home Assistant Integration**: Connect to smart home systems via HTTP API
- **Custom Device Extension**: Complete framework for device definition and registration

For supported device types and usage examples, please refer to the IoT Functionality Guide

## State Transition Diagram

```
                        +----------------+
                        |                |
                        v                |
+------+  Wake/Button  +------------+   |   +------------+
| IDLE | -----------> | CONNECTING | --+-> | LISTENING  |
+------+              +------------+       +------------+
   ^                                            |
   |                                            | Voice Recognition Complete
   |          +------------+                    v
   +--------- |  SPEAKING  | <-----------------+
     Playback +------------+
     Complete
```

## Upcoming Features
- [ ] **New GUI (Electron)**: Provides a more modern and beautiful user interface, optimizing the interaction experience.

## FAQ
- **Can't find audio device**: Please check if your microphone and speakers are properly connected and enabled.
- **Wake word not responding**: Check if the `USE_WAKE_WORD` setting in `config.json` is set to `true` and the model path is correct.
- **Network connection failure**: Check network settings and firewall configuration to ensure WebSocket or MQTT communication is not blocked.
- **Packaging failure**: Make sure PyInstaller is installed (`pip install pyinstaller`) and all dependencies are installed. Then re-execute `python scripts/build.py`
- **IoT devices not responding**: Check if the corresponding device configuration information is correct, such as Home Assistant URL and Token.

## Project Structure

```
├── .github                 # GitHub related configurations
├── assets                  # Resource files (emotion animations, etc.)
├── cache                   # Cache directory (music and temporary files)
├── config                  # Configuration directory
├── documents               # Documentation directory
├── hooks                   # PyInstaller hooks directory
├── libs                    # Dependencies directory
├── scripts                 # Utility scripts directory
├── src                     # Source code directory
│   ├── audio_codecs        # Audio encoding/decoding module
│   ├── audio_processing    # Audio processing module
│   ├── constants           # Constants definition
│   ├── display             # Display interface module
│   ├── iot                 # IoT device related module
│   │   └── things          # Specific device implementation directory
│   ├── network             # Network communication module
│   ├── protocols           # Communication protocol module
│   └── utils               # Utility classes module
```

## Contribution Guidelines
We welcome issue reports and code contributions. Please ensure you follow these specifications:

1. Code style complies with PEP8 standards
2. PR submissions include appropriate tests
3. Update relevant documentation

## Community and Support


### Sponsorship Support

<div align="center">
  <h3>Thanks to All Sponsors ❤️</h3>
  <p>Whether it's API resources, device compatibility testing, or financial support, every contribution makes the project more complete</p>
</div>

## License
MIT License 
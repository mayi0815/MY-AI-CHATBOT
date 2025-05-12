import argparse
import logging
import sys
import signal
import io
from src.application import Application
from src.utils.logging_config import setup_logging, get_logger

logger = get_logger(__name__)
# Configure logging

def parse_args():
    """Parse command line arguments"""
    # Ensure sys.stdout and sys.stderr are not None
    if sys.stdout is None:
        sys.stdout = io.StringIO()
    if sys.stderr is None:
        sys.stderr = io.StringIO()
        
    parser = argparse.ArgumentParser(description='Xiaozhi AI Client')
    
    # Add interface mode parameter
    parser.add_argument(
        '--mode', 
        choices=['gui', 'cli'],
        default='gui',
        help='Run mode: gui(graphical interface) or cli(command line)'
    )
    
    # Add protocol selection parameter
    parser.add_argument(
        '--protocol', 
        choices=['mqtt', 'websocket'], 
        default='websocket',
        help='Communication protocol: mqtt or websocket'
    )
    
    return parser.parse_args()

def signal_handler(sig, frame):
    """Handle Ctrl+C signal"""
    logger.info("Received interrupt signal, shutting down...")
    app = Application.get_instance()
    app.shutdown()
    sys.exit(0)


def main():
    """Program entry point"""
    # Register signal handler
    signal.signal(signal.SIGINT, signal_handler)
    # Parse command line arguments
    args = parse_args()
    try:
        # Setup logging
        setup_logging()
        # Create and run application
        app = Application.get_instance()

        logger.info("Application started, press Ctrl+C to exit")

        # Start application with parameters
        app.run(
            mode=args.mode,
            protocol=args.protocol
        )

        # If in GUI mode and using PyQt interface, start Qt event loop
        if args.mode == 'gui':
            # Get QApplication instance and run event loop
            try:
                from PyQt5.QtWidgets import QApplication
                qt_app = QApplication.instance()
                if qt_app:
                    logger.info("Starting Qt event loop")
                    qt_app.exec_()
                    logger.info("Qt event loop ended")
            except ImportError:
                logger.warning("PyQt5 not installed, cannot start Qt event loop")
            except Exception as e:
                logger.error(f"Qt event loop error: {e}", exc_info=True)

    except Exception as e:
        logger.error(f"Program error: {e}", exc_info=True)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
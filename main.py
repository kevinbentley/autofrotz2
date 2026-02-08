"""
AutoFrotz v2 - Entry Point

Multi-agent AI system for autonomously playing classic Infocom text
adventure games via the Frotz Z-Machine interpreter.
"""

import argparse
import logging
import sys
import threading

import uvicorn

from autofrotz.llm.factory import load_config
from autofrotz.orchestrator import Orchestrator


def main() -> None:
    """Parse arguments, load configuration, and run the game."""
    parser = argparse.ArgumentParser(
        description="AutoFrotz v2 - AI-powered text adventure player"
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to configuration file (default: config.json)",
    )
    parser.add_argument(
        "--game",
        help="Override game file from config",
    )
    parser.add_argument(
        "--max-turns",
        type=int,
        help="Override max turns from config",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[
            logging.StreamHandler(sys.stdout),
        ],
    )

    logger = logging.getLogger("autofrotz")

    try:
        # Load configuration
        config = load_config(args.config)

        # Apply command-line overrides
        if args.game:
            config["game_file"] = args.game
        if args.max_turns:
            config["max_turns"] = args.max_turns

        logger.info(
            f"AutoFrotz v2 starting: game={config.get('game_file')}, "
            f"max_turns={config.get('max_turns', 1000)}"
        )

        # Create and run the orchestrator
        orchestrator = Orchestrator(config)

        # Register hooks from config
        hook_names = config.get("hooks", [])
        hook_registry = {
            "web_monitor": "autofrotz.hooks.web_monitor.WebMonitorHook",
        }
        for hook_name in hook_names:
            if hook_name in hook_registry:
                module_path, class_name = hook_registry[hook_name].rsplit(".", 1)
                import importlib
                mod = importlib.import_module(module_path)
                hook_class = getattr(mod, class_name)
                orchestrator.register_hook(hook_class())
                logger.info(f"Registered hook: {hook_name}")
            else:
                logger.warning(f"Unknown hook: {hook_name}")

        # Start web server in a background thread so the game and web UI
        # share the same process (and the same connection_manager for
        # live WebSocket updates).
        web_config = config.get("web_server", {})
        web_host = web_config.get("host", "0.0.0.0")
        web_port = web_config.get("port", 8080)

        from autofrotz.web.server import app as web_app

        server_thread = threading.Thread(
            target=uvicorn.run,
            args=(web_app,),
            kwargs={"host": web_host, "port": web_port, "log_level": "warning"},
            daemon=True,
        )
        server_thread.start()
        logger.info(f"Web UI available at http://{web_host}:{web_port}")

        orchestrator.run()

    except FileNotFoundError as e:
        logger.error(f"File not found: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
        sys.exit(0)
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

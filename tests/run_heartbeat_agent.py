#!/usr/bin/env python3
"""
Test runner for the gProfiler agent with heartbeat mode enabled.

This script demonstrates how to run the gProfiler agent in heartbeat mode
to receive dynamic profiling commands from the Performance Studio backend.
"""

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional


def run_gprofiler_heartbeat_mode() -> int:
    """Run gProfiler in heartbeat mode"""

    # Configuration - adjust these values for your environment
    config: Dict[str, Any] = {
        "server_token": "test-token",
        "service_name": "test-service",
        "api_server": "http://localhost:8000",  # Performance Studio backend URL
        "server_host": "http://localhost:8000",  # Profile upload server URL (can be same)
        "output_dir": "/tmp/gprofiler-test",
        "log_file": "/tmp/gprofiler-heartbeat.log",
        "heartbeat_interval": "10",  # seconds
        "verbose": True,
    }

    # Ensure output directory exists
    output_dir = config["output_dir"]
    assert isinstance(output_dir, str)
    os.makedirs(output_dir, exist_ok=True)

    # Build the command
    gprofiler_path = Path(__file__).parent.parent / "gprofiler" / "main.py"

    cmd: list[str] = [
        sys.executable,
        str(gprofiler_path),
        "--enable-heartbeat-server",
        "--upload-results",
        "--token",
        str(config["server_token"]),
        "--service-name",
        str(config["service_name"]),
        "--api-server",
        str(config["api_server"]),
        "--server-host",
        str(config["server_host"]),
        "--output-dir",
        str(config["output_dir"]),
        "--log-file",
        str(config["log_file"]),
        "--heartbeat-interval",
        str(config["heartbeat_interval"]),
        "--no-verify",  # For testing with localhost
    ]

    if config["verbose"]:
        cmd.append("--verbose")

    print("🤖 Starting gProfiler in heartbeat mode...")
    print(f"📝 Command: {' '.join(cmd)}")
    print("=" * 60)
    print("The agent will:")
    print("1. Send heartbeats to the backend every 10 seconds")
    print("2. Wait for profiling commands from the server")
    print("3. Execute start/stop commands as received")
    print("4. Maintain idempotency for duplicate commands")
    print("=" * 60)
    print("💡 To test the system:")
    print("1. Start the Performance Studio backend")
    print("2. Run this script to start the agent")
    print("3. Use the backend API to send profiling requests")
    print("4. Watch the agent logs to see command execution")
    print("=" * 60)
    print("\n🚀 Starting agent... (Press Ctrl+C to stop)")

    try:
        # Start the process
        process: Optional[subprocess.Popen[str]] = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, bufsize=1
        )

        # Monitor output
        if process and process.stdout is not None:
            for line in iter(process.stdout.readline, ""):
                print(f"[AGENT] {line.rstrip()}")

        if process:
            process.wait()

    except KeyboardInterrupt:
        print("\n🛑 Received interrupt signal, stopping agent...")
        if process:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                print("⚠️  Process didn't stop gracefully, forcing termination...")
                process.kill()
                process.wait()

    except Exception as e:
        print(f"❌ Error running gProfiler: {e}")
        return 1

    print("✅ Agent stopped")
    return 0


def print_usage() -> None:
    """Print usage instructions"""
    print("📖 gProfiler Heartbeat Mode Test Runner")
    print("=" * 50)
    print("\nThis script runs gProfiler in heartbeat mode for testing.")
    print("\nPrerequisites:")
    print("1. Performance Studio backend running on http://localhost:8000")
    print("2. gProfiler agent code in the expected location")
    print("3. Python dependencies installed")
    print("\nUsage:")
    print(f"  {sys.argv[0]}")
    print("\nConfiguration:")
    print("- Edit the 'config' dictionary in this script to customize settings")
    print("- Logs will be written to /tmp/gprofiler-heartbeat.log")
    print("- Profiles will be saved to /tmp/gprofiler-test/")
    print("\nTesting flow:")
    print("1. Start the backend server")
    print("2. Run this script to start the agent")
    print("3. Use test_heartbeat_system.py to send commands")
    print("4. Watch the agent respond to commands")


def main() -> int:
    """Main function"""
    if len(sys.argv) > 1 and sys.argv[1] in ["-h", "--help"]:
        print_usage()
        return 0

    return run_gprofiler_heartbeat_mode()


if __name__ == "__main__":
    sys.exit(main())

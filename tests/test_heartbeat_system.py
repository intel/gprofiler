#!/usr/bin/env python3
"""
Test script to verify the heartbeat-based profiling control system.

This script demonstrates:
1. Agent sending heartbeat to backend
2. Backend responding with start/stop commands
3. Agent acting on commands with idempotency
4. Command completion acknowledgments

Supports both mock mode (default) and live mode with real backend.
"""

import sys
import time
import unittest.mock
from datetime import datetime
from typing import Any, Dict, Optional

import requests

# Configuration
BACKEND_URL = "http://localhost:8000"  # Adjust based on your setup
SERVICE_NAME = "test-service"
HOSTNAME = "test-host"
IP_ADDRESS = "127.0.0.1"

# Check if we should run in mock mode (no real backend)
MOCK_MODE = "--live" not in sys.argv  # Default to mock mode unless --live specified


class HeartbeatClient:
    """Client to simulate agent heartbeat behavior"""

    def __init__(self, backend_url: str, service_name: str, hostname: str, ip_address: str):
        self.backend_url = backend_url.rstrip("/")
        self.service_name = service_name
        self.hostname = hostname
        self.ip_address = ip_address
        self.last_command_id: Optional[str] = None
        self.executed_commands: set[str] = set()

    def send_heartbeat(self) -> Optional[Dict[str, Any]]:
        """Send heartbeat to backend and return response"""
        heartbeat_data = {
            "ip_address": self.ip_address,
            "hostname": self.hostname,
            "service_name": self.service_name,
            "last_command_id": self.last_command_id,
            "status": "active",
            "timestamp": datetime.now().isoformat(),
        }

        try:
            response = requests.post(f"{self.backend_url}/api/metrics/heartbeat", json=heartbeat_data, timeout=10)

            if response.status_code == 200:
                result = response.json()
                print(f"✓ Heartbeat successful: {result.get('message')}")

                if result.get("profiling_command") and result.get("command_id"):
                    command_id = result["command_id"]
                    profiling_command = result["profiling_command"]
                    command_type = profiling_command.get("command_type", "unknown")

                    print(f"📋 Received command: {command_type} (ID: {command_id})")

                    # Check idempotency
                    if command_id in self.executed_commands:
                        print(f"⚠️  Command {command_id} already executed, skipping...")
                        return None

                    # Mark as executed
                    self.executed_commands.add(command_id)
                    self.last_command_id = command_id

                    return {
                        "command_type": command_type,
                        "command_id": command_id,
                        "profiling_command": profiling_command,
                    }
                else:
                    print("📭 No pending commands")
                return None
            else:
                print(f"❌ Heartbeat failed: {response.status_code} - {response.text}")
                return None

        except Exception as e:
            print(f"❌ Heartbeat error: {e}")
            return None

    def send_command_completion(
        self,
        command_id: str,
        status: str,
        execution_time: int = 0,
        error_message: Optional[str] = None,
        results_path: Optional[str] = None,
    ) -> bool:
        """Send command completion status to backend"""
        completion_data = {
            "command_id": command_id,
            "hostname": self.hostname,
            "status": status,
            "execution_time": execution_time,
            "error_message": error_message,
            "results_path": results_path,
        }

        try:
            response = requests.post(
                f"{self.backend_url}/api/metrics/command_completion", json=completion_data, timeout=10
            )

            if response.status_code == 200:
                print(f"✅ Command completion sent successfully for {command_id} with status: {status}")
                return True
            else:
                print(f"❌ Failed to send command completion: {response.status_code} - {response.text}")
                return False

        except Exception as e:
            print(f"❌ Error sending command completion: {e}")
            return False

    def simulate_profiling_action(self, command_type: str, command_id: str) -> None:
        """Simulate profiling action (start/stop)"""
        if command_type == "start":
            print(f"🚀 Starting profiler for command {command_id}")
            # Simulate profiling work
            time.sleep(2)
            print("✅ Profiler completed successfully")
            # Send completion acknowledgment
            self.send_command_completion(command_id, "completed", execution_time=2)
        elif command_type == "stop":
            print(f"🛑 Stopping profiler for command {command_id}")
            # Simulate stopping
            time.sleep(1)
            print("✅ Profiler stopped successfully")
            # Send completion acknowledgment
            self.send_command_completion(command_id, "completed", execution_time=1)
        else:
            print(f"⚠️  Unknown command type: {command_type}")
            # Send failure acknowledgment
            self.send_command_completion(command_id, "failed", error_message=f"Unknown command type: {command_type}")


def create_test_profiling_request(backend_url: str, service_name: str, command_type: str = "start") -> bool:
    """Create a test profiling request"""
    request_data = {
        "service_name": service_name,
        "command_type": command_type,
        "duration": 60,
        "frequency": 11,
        "profiling_mode": "cpu",
        "target_hostnames": [HOSTNAME],
        "additional_args": {"test": True},
    }

    try:
        response = requests.post(f"{backend_url}/api/metrics/profile_request", json=request_data, timeout=10)

        if response.status_code == 200:
            result = response.json()
            print(f"✅ Profiling request created: {result.get('message')}")
            print(f"   Request ID: {result.get('request_id')}")
            print(f"   Command ID: {result.get('command_id')}")
            return True
        else:
            print(f"❌ Failed to create profiling request: {response.status_code} - {response.text}")
            return False

    except Exception as e:
        print(f"❌ Error creating profiling request: {e}")
        return False


def create_mock_responses() -> tuple[Any, Dict[str, Any]]:
    """Create mock responses for testing without a real backend"""
    mock_state: Dict[str, Any] = {"pending_commands": [], "completed_commands": [], "heartbeat_count": 0}

    def mock_heartbeat_post(url: str, json: Optional[Any] = None, timeout: Optional[Any] = None) -> Any:  # noqa: F811
        """Mock heartbeat endpoint"""
        json_data = json if json is not None else {}
        mock_state["heartbeat_count"] += 1

        # Mock response object
        response = unittest.mock.Mock()
        response.status_code = 200

        # Check if there are pending commands
        if mock_state["pending_commands"]:
            command = mock_state["pending_commands"].pop(0)
            response.json.return_value = {
                "message": "Heartbeat received",
                "command_id": command["command_id"],
                "profiling_command": command["profiling_command"],
            }
        else:
            response.json.return_value = {"message": "Heartbeat received, no pending commands"}

        return response

    def mock_profile_request_post(
        url: str, json: Optional[Any] = None, timeout: Optional[Any] = None
    ) -> Any:  # noqa: F811
        """Mock profile request endpoint"""
        json_data = json if json is not None else {}
        # Generate unique IDs based on total requests made
        total_requests = len(mock_state["completed_commands"]) + len(mock_state["pending_commands"]) + 1
        command_id = f"cmd_{total_requests}"
        request_id = f"req_{total_requests}"

        # Add command to pending queue
        mock_state["pending_commands"].append(
            {
                "command_id": command_id,
                "profiling_command": {
                    "command_type": json_data.get("command_type", "start"),
                    "combined_config": {
                        "duration": json_data.get("duration", 60),
                        "frequency": json_data.get("frequency", 11),
                        "profiling_mode": json_data.get("profiling_mode", "cpu"),
                    },
                },
            }
        )

        response = unittest.mock.Mock()
        response.status_code = 200
        response.json.return_value = {
            "message": "Profiling request created",
            "request_id": request_id,
            "command_id": command_id,
        }

        return response

    def mock_command_completion_post(
        url: str, json: Optional[Any] = None, timeout: Optional[Any] = None
    ) -> Any:  # noqa: F811
        """Mock command completion endpoint"""
        json_data = json if json is not None else {}
        mock_state["completed_commands"].append(
            {
                "command_id": json_data.get("command_id"),
                "status": json_data.get("status"),
                "execution_time": json_data.get("execution_time"),
            }
        )

        response = unittest.mock.Mock()
        response.status_code = 200
        response.json.return_value = {"message": "Command completion received"}

        return response

    def mock_post(url: str, json: Optional[Any] = None, timeout: Optional[Any] = None) -> Any:  # noqa: F811
        """Route mock requests to appropriate handlers"""
        if "/heartbeat" in url:
            return mock_heartbeat_post(url, json, timeout)
        elif "/profile_request" in url:
            return mock_profile_request_post(url, json, timeout)
        elif "/command_completion" in url:
            return mock_command_completion_post(url, json, timeout)
        else:
            # Unknown endpoint
            response = unittest.mock.Mock()
            response.status_code = 404
            response.text = "Not found"
            return response

    return mock_post, mock_state


def run_tests() -> None:
    """Run the actual test logic"""

    # Initialize test client
    client = HeartbeatClient(BACKEND_URL, SERVICE_NAME, HOSTNAME, IP_ADDRESS)

    # Test 1: Send initial heartbeat (should have no commands)
    print("\n1️⃣  Test: Initial heartbeat (no commands expected)")
    client.send_heartbeat()

    # Test 2: Create a START profiling request
    print("\n2️⃣  Test: Create START profiling request")
    if create_test_profiling_request(BACKEND_URL, SERVICE_NAME, "start"):
        time.sleep(0.1)  # Give backend time to process

        # Send heartbeat to receive the command
        print("\n   📡 Sending heartbeat to receive command...")
        command = client.send_heartbeat()

        if command:
            client.simulate_profiling_action(command["command_type"], command["command_id"])

        # Test idempotency - send heartbeat again
        print("\n   🔄 Testing idempotency - sending heartbeat again...")
        command = client.send_heartbeat()
        if command is None:
            print("✅ Idempotency working - no duplicate command received")

    # Test 3: Create a STOP profiling request
    print("\n3️⃣  Test: Create STOP profiling request")
    if create_test_profiling_request(BACKEND_URL, SERVICE_NAME, "stop"):
        time.sleep(0.1)  # Give backend time to process

        # Send heartbeat to receive the stop command
        print("\n   📡 Sending heartbeat to receive stop command...")
        command = client.send_heartbeat()

        if command:
            client.simulate_profiling_action(command["command_type"], command["command_id"])

    # Test 4: Multiple heartbeats with no commands
    print("\n4️⃣  Test: Multiple heartbeats with no pending commands")
    for i in range(3):
        print(f"\n   Heartbeat {i+1}/3:")
        client.send_heartbeat()
        time.sleep(0.1)

    print("\n✅ Test completed!")
    print("\nTest Summary:")
    print(f"   - Executed commands: {len(client.executed_commands)}")
    print(f"   - Last command ID: {client.last_command_id}")
    print(f"   - Commands executed: {list(client.executed_commands)}")


def main() -> None:
    """Main test function"""
    print("🧪 Testing Heartbeat-Based Profiling Control System")

    if MOCK_MODE:
        print("🎭 Running in MOCK MODE (no real backend required)")
        print("   Use --live flag to test against real backend on localhost:8000")
        mock_post, mock_state = create_mock_responses()

        # Patch requests.post for mock mode
        with unittest.mock.patch("requests.post", side_effect=mock_post):
            print("=" * 60)
            run_tests()

        # Print mock state summary
        print("\n📊 Mock Backend State:")
        print(f"   - Total heartbeats: {mock_state['heartbeat_count']}")
        print(f"   - Pending commands: {len(mock_state['pending_commands'])}")
        print(f"   - Completed commands: {len(mock_state['completed_commands'])}")

        if mock_state["completed_commands"]:
            print("   - Command completions:")
            for cmd in mock_state["completed_commands"]:
                print(f"     * {cmd['command_id']}: {cmd['status']} ({cmd['execution_time']}s)")

    else:
        print("🌐 Running in LIVE MODE (requires backend on localhost:8000)")
        print("=" * 60)
        run_tests()


if __name__ == "__main__":
    main()

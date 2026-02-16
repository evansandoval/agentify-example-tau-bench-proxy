#!/usr/bin/env python3
"""
Tau Proxy Test Script

Automates the full proxy agent test flow:
1. Creates green and white proxied agents via the backend API
2. Launches `agentbeats run_ctrl` for each agent
3. Waits for both agents to be ready
4. Creates a tau-bench assessment
5. Opens the frontend to view results

Usage:
    export AB_API_KEY="your-api-key-here"
    python proxy_test.py [--backend-url https://backend.evansandoval.org]

The AB_API_KEY is the cookie value from your browser session after logging in
via GitHub OAuth. You can find it in your browser's developer tools under
Application > Cookies > ab_api_key.
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
import uuid

import requests

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
GREEN_AGENT_DIR = os.path.join(SCRIPT_DIR, "src", "green_agent")
WHITE_AGENT_DIR = os.path.join(SCRIPT_DIR, "src", "white_agent")

CHECK_READY_TIMEOUT = 90  # seconds
CHECK_READY_INTERVAL = 3  # seconds


def parse_args():
    parser = argparse.ArgumentParser(description="Run tau-bench proxy agent test")
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("AB_BACKEND_URL", "https://backend.evansandoval.org"),
        help="Backend API URL (default: https://backend.evansandoval.org)",
    )
    parser.add_argument(
        "--repeat-n",
        type=int,
        default=1,
        help="Number of assessment repetitions (default: 1)",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open the frontend in a browser",
    )
    return parser.parse_args()


def get_api_key():
    api_key = os.environ.get("AB_API_KEY")
    if not api_key:
        print("Error: AB_API_KEY environment variable is required.")
        print("Set it to your ab_api_key cookie value from the browser after logging in.")
        sys.exit(1)
    return api_key


def get_user_info(backend_url, cookies):
    """Fetch the current user's info to get github_id."""
    resp = requests.get(f"{backend_url}/user", cookies=cookies)
    if resp.status_code != 200:
        print(f"Error: Failed to fetch user info: {resp.status_code} {resp.text}")
        sys.exit(1)
    return resp.json()


def create_agent(backend_url, cookies, name, is_green, user_id):
    """Create a proxied agent via the backend API."""
    payload = {
        "name": name,
        "is_green": is_green,
        "deploy_type": "proxied",
        "secret": "",
        "inject_litellm_proxy_api": False,
        "ctrl_url": None,
        "git_url": None,
        "git_branch": None,
        "docker_image_url": None,
        "description_prompt": None,
        "user_id": user_id,
    }
    resp = requests.post(f"{backend_url}/agents/", json=payload, cookies=cookies)
    if resp.status_code != 200:
        print(f"Error: Failed to create agent '{name}': {resp.status_code} {resp.text}")
        sys.exit(1)
    agent = resp.json()
    print(f"  Created agent '{name}' -> id={agent['id']}")
    return agent


def find_free_port():
    """Find a free port by briefly binding to port 0."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def start_controller(agent_id, agent_dir, ctrl_port):
    """Launch `agentbeats run_ctrl --proxy-agent-id=<id>` in the given directory."""
    cmd = ["agentbeats", "run_ctrl", f"--proxy-agent-id={agent_id}"]
    api_key = os.environ.get("AB_API_KEY", "")
    env = {**os.environ, "PORT": str(ctrl_port), "AB_API_KEY": api_key}
    print(f"  Starting controller in {os.path.basename(agent_dir)}/ (ctrl port {ctrl_port}): {' '.join(cmd)}")
    proc = subprocess.Popen(cmd, cwd=agent_dir, env=env)
    return proc


def wait_for_agent_ready(backend_url, cookies, agent_id, agent_name, timeout=CHECK_READY_TIMEOUT):
    """Poll the most_recent_check endpoint until the agent is ready, triggering new checks as needed."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            # Trigger a fresh check
            requests.get(f"{backend_url}/agents/{agent_id}/check_again", cookies=cookies)

            # Wait a moment for the check to complete
            time.sleep(CHECK_READY_INTERVAL)

            resp = requests.get(
                f"{backend_url}/agents/{agent_id}/most_recent_check",
                cookies=cookies,
            )
            if resp.status_code == 200:
                check = resp.json()
                reachable = check.get("is_ctrl_reachable")
                agent_count = check.get("agent_count")
                if reachable and agent_count and agent_count >= 1:
                    print(f"  {agent_name} is ready (ctrl reachable, {agent_count} agent(s))")
                    return True
                else:
                    status = f"reachable={reachable}, agents={agent_count}"
                    print(f"  {agent_name} not ready yet ({status}), triggering new check...")
            elif resp.status_code == 404:
                print(f"  {agent_name} has no checks yet, triggering new check...")
            else:
                print(f"  {agent_name} check returned {resp.status_code}, retrying...")
        except requests.RequestException as e:
            print(f"  {agent_name} check request failed: {e}, retrying...")
            time.sleep(CHECK_READY_INTERVAL)

    print(f"Error: {agent_name} did not become ready within {timeout}s")
    return False


def create_assessment(backend_url, cookies, green_agent_id, white_agent_id, repeat_n):
    """Create a tau-bench assessment."""
    payload = {
        "agents": [green_agent_id, white_agent_id],
        "config": "tau-bench",
        "repeat_n": repeat_n,
    }
    resp = requests.post(f"{backend_url}/assessments/", json=payload, cookies=cookies)
    if resp.status_code != 200:
        print(f"Error: Failed to create assessment: {resp.status_code} {resp.text}")
        sys.exit(1)
    assessment_ids = resp.json()
    print(f"  Created {len(assessment_ids)} assessment(s): {assessment_ids}")
    return assessment_ids


def delete_agent(backend_url, cookies, agent_id, agent_name):
    """Delete an agent from the backend."""
    try:
        resp = requests.delete(f"{backend_url}/agents/{agent_id}", cookies=cookies)
        if resp.status_code == 200:
            print(f"  Deleted agent '{agent_name}' ({agent_id})")
        else:
            print(f"  Warning: Failed to delete agent '{agent_name}': {resp.status_code}")
    except requests.RequestException:
        print(f"  Warning: Could not reach backend to delete agent '{agent_name}'")


def main():
    args = parse_args()
    api_key = get_api_key()
    cookies = {"ab_api_key": api_key}
    backend_url = args.backend_url.rstrip("/")

    instance_id = uuid.uuid4().hex[:8]
    green_name = f"tau-green-{instance_id}"
    white_name = f"tau-white-{instance_id}"

    procs = []
    green_agent = None
    white_agent = None

    def cleanup(signum=None, _frame=None):
        print("\nCleaning up...")
        for proc in procs:
            if proc.poll() is None:
                print(f"  Terminating controller (pid={proc.pid})")
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if green_agent:
            delete_agent(backend_url, cookies, green_agent["id"], green_name)
        if white_agent:
            delete_agent(backend_url, cookies, white_agent["id"], white_name)
        if signum is not None:
            sys.exit(1)

    signal.signal(signal.SIGINT, cleanup)
    signal.signal(signal.SIGTERM, cleanup)

    # Step 1: Get user info
    print(f"Instance ID: {instance_id}")
    print(f"Backend URL: {backend_url}")
    print()

    print("[1/5] Fetching user info...")
    user = get_user_info(backend_url, cookies)
    user_id = user["github_id"]
    print(f"  Logged in as: {user['display_name']} (github_id={user_id})")
    print()

    # Step 2: Create agents
    print("[2/5] Creating proxied agents...")
    green_agent = create_agent(backend_url, cookies, green_name, True, user_id)
    white_agent = create_agent(backend_url, cookies, white_name, False, user_id)
    print()

    # Step 3: Start controllers
    print("[3/5] Starting controllers...")
    green_port = find_free_port()
    white_port = find_free_port()
    green_proc = start_controller(green_agent["id"], GREEN_AGENT_DIR, green_port)
    white_proc = start_controller(white_agent["id"], WHITE_AGENT_DIR, white_port)
    procs.extend([green_proc, white_proc])
    print("  Waiting a few seconds for controllers to initialize...")
    time.sleep(5)
    print()

    # Step 4: Wait for readiness
    print("[4/5] Waiting for agents to be ready...")
    green_ready = wait_for_agent_ready(backend_url, cookies, green_agent["id"], green_name)
    white_ready = wait_for_agent_ready(backend_url, cookies, white_agent["id"], white_name)
    if not (green_ready and white_ready):
        print("Error: One or both agents failed to become ready. Aborting.")
        cleanup()
        sys.exit(1)
    print()

    # Step 5: Create assessment
    print("[5/5] Creating tau-bench assessment...")
    assessment_ids = create_assessment(
        backend_url, cookies, green_agent["id"], white_agent["id"], args.repeat_n
    )
    print()

    # Report results
    frontend_url = backend_url.replace("backend.", "")
    first_assessment_id = assessment_ids[0]
    results_url = f"{frontend_url}/assessments/{first_assessment_id}"
    print("Assessment submitted! View results at:")
    print(f"  {results_url}")

    # Keep running so controllers stay alive during the assessment
    print()
    print("Controllers are running. Press Ctrl+C to stop and clean up.")
    try:
        while True:
            # Check if either process has died
            for proc in procs:
                if proc.poll() is not None:
                    print(f"Warning: Controller (pid={proc.pid}) exited with code {proc.returncode}")
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        cleanup()


if __name__ == "__main__":
    main()

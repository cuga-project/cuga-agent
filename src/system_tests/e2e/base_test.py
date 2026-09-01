import json
import os
import traceback
import unittest
from typing import Any, Dict, List, Optional, Tuple

import httpx

from cuga.backend.cuga_graph.nodes.human_in_the_loop.followup_model import (
    ActionResponse,
)
from cuga.config import settings

SERVER_URL = f"http://localhost:{settings.server_ports.demo}"
STREAM_ENDPOINT = f"{SERVER_URL}/stream"
STOP_ENDPOINT = f"{SERVER_URL}/stop"
MCP_SERVERS_FILE_PATH = os.path.join(os.path.dirname(__file__), "config", "mcp_servers.yaml")


def _apply_base_test_env_defaults() -> None:
    os.environ["MCP_SERVERS_FILE"] = MCP_SERVERS_FILE_PATH
    os.environ["CUGA_TEST_ENV"] = "true"
    os.environ["DYNACONF_ADVANCED_FEATURES__TRACKER_ENABLED"] = "true"
    os.environ["DYNACONF_POLICY__ENABLED"] = "false"
    os.environ.setdefault(
        "DYNACONF_SERVER_PORTS__DIGITAL_SALES_API",
        str(settings.server_ports.digital_sales_api),
    )


class BaseTestServerStream(unittest.IsolatedAsyncioTestCase):
    _stability_stack = "digital_sales"
    test_env_vars = {}

    def _parse_event_data(self, data_str: str) -> Any:
        try:
            parsed_json = json.loads(data_str)
            if isinstance(parsed_json, dict) and "data" in parsed_json:
                return parsed_json["data"]
            return parsed_json
        except json.JSONDecodeError:
            return data_str

    def get_event_at(self, all_data: List[Dict[str, Any]], n: int) -> Tuple[str, str]:
        last_event = all_data[n]
        last_event_key = last_event['event']
        last_event_value = last_event.get('data', 'N/A')
        return last_event_key, last_event_value

    async def run_task(
        self,
        query: str,
        followup_response: Optional[ActionResponse] = None,
        stop_on_answer: bool = True,
        timeout: Optional[float] = None,
        verbose: bool = True,
        thread_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        all_events = []

        if verbose:
            print(f"\n--- Running task for query: '{query}' ---")
            if thread_id:
                print(f"Using thread ID: {thread_id}")

        try:
            if verbose:
                print(f"Sending POST request to {STREAM_ENDPOINT} with query: '{query}'")

            client_timeout = httpx.Timeout(timeout) if timeout else None
            headers = {"Accept": "text/event-stream"}
            if thread_id:
                headers["X-Thread-ID"] = thread_id

            async with httpx.AsyncClient(timeout=client_timeout) as client:
                async with client.stream(
                    "POST",
                    STREAM_ENDPOINT,
                    json={"query": query} if query and query != "" else followup_response.model_dump(),
                    headers=headers,
                ) as response:
                    response.raise_for_status()

                    content_type = response.headers.get("content-type", "")
                    if "text/event-stream" not in content_type and verbose:
                        print(f"Warning: Expected 'text/event-stream', got '{content_type}'")

                    buffer = b""
                    async for chunk in response.aiter_bytes():
                        buffer += chunk

                        while b"\n\n" in buffer:
                            event_block, buffer = buffer.split(b"\n\n", 1)
                            event_lines = event_block.split(b"\n")

                            event_data = {}
                            for line in event_lines:
                                line = line.strip()
                                if not line:
                                    continue

                                if line.startswith(b"event: "):
                                    event_data["event"] = line[len(b"event: ") :].decode("utf-8").strip()
                                elif line.startswith(b"data: "):
                                    try:
                                        data_str = line[len(b"data: ") :].decode("utf-8").strip()
                                        event_data["data"] = self._parse_event_data(data_str)
                                    except UnicodeDecodeError:
                                        event_data["data"] = line[len(b"data: ") :].strip()
                                else:
                                    try:
                                        line_str = line.decode("utf-8").strip()
                                        if ":" not in line_str and not event_data.get("event"):
                                            event_data["event"] = line_str
                                        elif ":" not in line_str and not event_data.get("data"):
                                            event_data["data"] = self._parse_event_data(line_str)
                                    except UnicodeDecodeError:
                                        continue

                            if event_data and (event_data.get("event") or event_data.get("data")):
                                all_events.append(event_data)

                                if verbose:
                                    print(f"Received Event: {event_data.get('event', 'N/A')}")
                                    print(f"  Data: {event_data.get('data', 'N/A')}\n")

                                if stop_on_answer and event_data.get("event") == "Answer":
                                    if verbose:
                                        print("--- 'Answer' event received, stopping stream. ---")

        except httpx.RequestError as exc:
            print(f"Request URL: {exc.request.url!r}")
            print(f"Request Method: {exc.request.method}")
            print(f"Exception Type: {type(exc).__name__}")
            print(f"Exception Message: {exc}")
            print("Full Traceback:")
            traceback.print_exc()
            print("--- End HTTP Request Error ---\n")
        except Exception as e:
            print("\n--- Unexpected Error Occurred ---")
            print(f"Exception Type: {type(e).__name__}")
            print(f"Exception Message: {e}")
            print("Full Traceback:")
            raise Exception(f"An unexpected error occurred during stream processing: {e}")

        if verbose:
            print(f"\n--- Task completed. Total events received: {len(all_events)} ---")

        return all_events

    @staticmethod
    def get_state_chat_messages_count(state_response: dict) -> int:
        state = state_response.get("state") or {}
        return int(state.get("chat_messages_count") or 0)

    @staticmethod
    def get_state_variables_count(state_response: dict) -> int:
        return int(state_response.get("variables_count") or 0)

    def _assert_answer_event(
        self,
        all_events: List[Dict[str, Any]],
        expected_keywords: List[str] = None,
        keyword_match_mode: str = "all",
        operator: str = "contains",
    ):
        print("\n--- Performing assertions ---")

        self.assertGreater(len(all_events), 0, "No events were received from the stream.")

        answer_event = next((e for e in all_events if e.get("event") == "Answer"), None)

        self.assertIsNotNone(answer_event, "The 'Answer' event was not found in the stream.")
        print("Assertion Passed: 'Answer' event found.")

        answer_data = answer_event.get("data")
        self.assertIsNotNone(answer_data, "The 'Answer' event has no data.")
        self.assertNotEqual(answer_data, "", "The 'Answer' event data is empty.")
        print("Assertion Passed: 'Answer' data is not empty.")

        if expected_keywords:
            answer_str = str(answer_data).lower()
            answer_str = answer_str.replace("\u202f", " ")

            if operator == "not_contains":
                if keyword_match_mode == "any":
                    absent_keywords = [kw for kw in expected_keywords if kw.lower() not in answer_str]
                    self.assertTrue(
                        len(absent_keywords) > 0,
                        f"Answer should not contain at least one of the keywords: {expected_keywords}. Got: {answer_str}",
                    )
                    print(
                        f"Assertion Passed: Answer does not contain at least one keyword. Absent: {absent_keywords}"
                    )
                else:
                    for keyword in expected_keywords:
                        self.assertNotIn(
                            keyword.lower(), answer_str, f"Answer should not contain '{keyword}' but it does."
                        )
                    print(
                        f"Assertion Passed: Answer does not contain any of the keywords: {expected_keywords}"
                    )
            else:
                if keyword_match_mode == "any":
                    matched_keywords = [kw for kw in expected_keywords if kw.lower() in answer_str]
                    self.assertTrue(
                        len(matched_keywords) > 0,
                        f"Answer does not contain any of the expected keywords: {expected_keywords}. Got: {answer_str}",
                    )
                    print(
                        f"Assertion Passed: Answer contains at least one expected keyword. Matched: {matched_keywords}"
                    )
                else:
                    for keyword in expected_keywords:
                        self.assertIn(keyword.lower(), answer_str, f"Answer does not contain '{keyword}'.")
                    print(f"Assertion Passed: Answer contains all expected keywords: {expected_keywords}")

        print("\n--- All assertions passed! ---")

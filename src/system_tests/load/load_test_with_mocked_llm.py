import asyncio
import uuid
import time
import os
import httpx
import pytest
from system_tests.e2e.base_test import BaseTestServerStream, SERVER_URL
from system_tests.load.conftest import resolve_load_test_users
from system_tests.load.isolation import (
    ThreadStateExpectations,
    extract_thread_state_snapshot,
    validate_thread_state_counts,
    validate_threads_state_uniformity,
)
from system_tests.load.metrics import (
    LoadTestConcurrencyReport,
    UserLoadTimings,
    print_load_test_report,
)

STATE_ENDPOINT = f"{SERVER_URL}/api/agent/state"


pytestmark = pytest.mark.load


class LoadTestWithMockedLLM(BaseTestServerStream):
    """
    Concurrent load test using deterministic mock LLM responses.

    Enables ``CUGA_MOCK_LLM`` so the demo server never calls external LLM APIs.
    Validates per-thread agent state, chat message history, and answer isolation.
    """

    test_env_vars = {
        "CUGA_MODE": "api",
        "CUGA_TEST_ENV": "true",
        "CUGA_MOCK_LLM": "true",
        "DYNACONF_SERVER_PORTS__DIGITAL_SALES_API": "8000",
        "DYNACONF_SERVER_PORTS__REGISTRY": "8001",
        "DYNACONF_SERVER_PORTS__DEMO": "7860",
        "DYNACONF_ADVANCED_FEATURES__TRACKER_ENABLED": "false",
        "DYNACONF_ADVANCED_FEATURES__LITE_MODE": "true",
    }

    test_state_isolation = True
    check_chat_messages_isolation = True
    num_users = 5
    expected_primary_variables_count = 3
    expected_primary_chat_messages_count = 4
    expected_final_variables_count = 3
    expected_final_chat_messages_count = 6

    test_e2b_mode = os.getenv("CUGA_E2B_MODE", "false").lower() == "true"

    def setUp(self):
        self.num_users = resolve_load_test_users(self.num_users)
        super().setUp()
        if self.test_e2b_mode:
            from cuga.config import settings as cuga_settings

            if not os.getenv("E2B_API_KEY"):
                raise Exception("E2B_API_KEY not found in environment")
            if not cuga_settings.server_ports.function_call_host:
                raise Exception("settings.server_ports.function_call_host not found in settings.toml")

            self.test_env_vars["DYNACONF_ADVANCED_FEATURES__E2B_SANDBOX"] = "true"
            print("E2B mode enabled")

    async def get_agent_state(self, thread_id: str) -> dict:
        """Get agent state for a specific thread_id."""
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                STATE_ENDPOINT,
                headers={"X-Thread-ID": thread_id},
            )
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 503:
                return {
                    "thread_id": thread_id,
                    "state": None,
                    "variables": {},
                    "variables_count": 0,
                }
            else:
                raise Exception(f"Failed to get state: {response.status_code} - {response.text}")

    def _extract_answer_text(self, events: list[dict]) -> str:
        answer_event = next((e for e in events if e.get("event") == "Answer"), None)
        if not answer_event:
            return ""
        return str(answer_event.get("data", ""))

    async def validate_own_thread_state(
        self,
        user_id: int,
        thread_id: str,
        *,
        expectations: ThreadStateExpectations | None = None,
    ) -> tuple[bool, str]:
        """Validate this thread has scoped state with expected variables and chat history."""
        if expectations is None:
            expectations = ThreadStateExpectations(
                variables_count=self.expected_primary_variables_count,
                chat_messages_count=self.expected_primary_chat_messages_count,
            )
        try:
            state_response = await self.get_agent_state(thread_id)
            return validate_thread_state_counts(
                state_response,
                user_id=user_id,
                thread_id=thread_id,
                expectations=expectations,
                check_chat_messages=self.check_chat_messages_isolation,
                get_variables_count=self.get_state_variables_count,
                get_chat_messages_count=self.get_state_chat_messages_count,
            )
        except Exception as e:
            return False, f"User {user_id}: Error validating state: {e}"

    async def validate_all_threads_isolated(self, thread_ids: list[str]) -> tuple[bool, str]:
        """After all concurrent users finish, verify uniform per-thread state counts."""
        snapshots = []
        for user_id, thread_id in enumerate(thread_ids):
            state_response = await self.get_agent_state(thread_id)
            snapshots.append(
                extract_thread_state_snapshot(
                    user_id,
                    thread_id,
                    state_response,
                    get_variables_count=self.get_state_variables_count,
                    get_chat_messages_count=self.get_state_chat_messages_count,
                )
            )

        expectations = ThreadStateExpectations(
            variables_count=self.expected_final_variables_count,
            chat_messages_count=self.expected_final_chat_messages_count,
        )
        ok, error, report = validate_threads_state_uniformity(
            snapshots,
            expectations=expectations,
            check_chat_messages=self.check_chat_messages_isolation,
        )
        if report:
            print(f"\n{report}")
        if not ok:
            return False, error
        return True, ""

    async def validate_response_isolation(
        self,
        user_id: int,
        thread_id: str,
        primary_answer: str,
        followup_answer: str,
    ) -> tuple[bool, str]:
        """Ensure answers are present, distinct, and scoped to this user's run."""
        if "50" not in primary_answer.lower():
            return False, f"User {user_id}: primary answer missing expected count: {primary_answer[:120]}"
        if "50" not in followup_answer.lower():
            return False, f"User {user_id}: followup answer missing expected count: {followup_answer[:120]}"
        if primary_answer.strip() == followup_answer.strip():
            return False, f"User {user_id}: primary and followup answers are identical"
        if thread_id and thread_id in primary_answer:
            return False, f"User {user_id}: primary answer leaked thread_id"
        return True, ""

    async def run_single_user_task(
        self, user_id: int, thread_id: str, all_thread_ids: list[str], batch_start: float
    ) -> tuple[bool, str, UserLoadTimings | None]:
        """
        Runs a task for a single user and verifies the result.
        Returns (success, error_message, timings)
        """
        query = "list all my accounts, how many are there?"
        expected_keywords = ["50"]
        timings = UserLoadTimings(user_id=user_id, thread_id=thread_id, started_at=time.monotonic())

        print(f"User {user_id} (Thread {thread_id}): Starting task...")

        try:
            if self.test_state_isolation:
                initial_state = await self.get_agent_state(thread_id)
                if initial_state.get("thread_id") != thread_id:
                    return False, f"User {user_id}: initial state thread_id mismatch", timings

                initial_variables_count = self.get_state_variables_count(initial_state)
                initial_chat_messages_count = self.get_state_chat_messages_count(initial_state)

                if initial_variables_count > 0:
                    return (
                        False,
                        f"User {user_id}: State should be empty at start, but found {initial_variables_count} variables",
                        timings,
                    )
                if self.check_chat_messages_isolation and initial_chat_messages_count > 0:
                    return (
                        False,
                        f"User {user_id}: chat_messages should be empty at start, but found {initial_chat_messages_count}",
                        timings,
                    )

            primary_started = time.monotonic()
            all_events = await self.run_task(query=query, thread_id=thread_id, verbose=False, timeout=60.0)
            timings.primary_finished_at = time.monotonic()
            timings.primary_duration_s = timings.primary_finished_at - primary_started

            try:
                self._assert_answer_event(all_events, expected_keywords=expected_keywords)
            except AssertionError as e:
                return False, f"User {user_id}: {str(e)}", timings

            primary_answer = self._extract_answer_text(all_events)

            if self.test_state_isolation:
                state_check_started = time.monotonic()

                # Poll until the background history save lands instead of a fixed 1s sleep,
                # which is both slower than needed and flaky under load.
                is_ok, state_error = False, "state check did not run"
                state_check_timeout_s = 5.0
                while time.monotonic() - state_check_started < state_check_timeout_s:
                    is_ok, state_error = await self.validate_own_thread_state(user_id, thread_id)
                    if is_ok:
                        break
                    await asyncio.sleep(0.1)

                timings.state_checked_at = time.monotonic()
                timings.state_check_duration_s = timings.state_checked_at - state_check_started
                if not is_ok:
                    return False, state_error, timings

                print(f"User {user_id}: ✓ Thread state checkpointed")

            print(f"User {user_id} (Thread {thread_id}): Sending followup question...")
            followup_query = "how many accounts did we retrieve?"
            followup_expected_keywords = ["50"]

            followup_started = time.monotonic()
            all_followup_events = await self.run_task(
                query=followup_query, thread_id=thread_id, verbose=False, timeout=60.0
            )
            timings.followup_duration_s = time.monotonic() - followup_started

            try:
                self._assert_answer_event(all_followup_events, expected_keywords=followup_expected_keywords)
            except AssertionError as e:
                return False, f"User {user_id}: Followup - {str(e)}", timings

            followup_answer = self._extract_answer_text(all_followup_events)
            response_ok, response_error = await self.validate_response_isolation(
                user_id, thread_id, primary_answer, followup_answer
            )
            if not response_ok:
                return False, response_error, timings

            timings.finished_at = time.monotonic()
            timings.total_duration_s = timings.finished_at - timings.started_at

            print(f"User {user_id}: ✓ Followup question answered correctly")
            print(f"User {user_id}: Success!")
            return True, "", timings

        except Exception as e:
            timings.finished_at = time.monotonic()
            timings.total_duration_s = timings.finished_at - timings.started_at
            return False, f"User {user_id}: Exception: {e}", timings

    async def test_concurrent_users_with_mocked_llm(self):
        """Simulate concurrent users with mock LLM and strict isolation checks."""
        print(f"\n--- Starting Mocked LLM Load Test with {self.num_users} users ---")

        batch_start = time.monotonic()
        thread_ids = [str(uuid.uuid4()) for _ in range(self.num_users)]

        tasks = [
            self.run_single_user_task(i, thread_ids[i], thread_ids, batch_start)
            for i in range(self.num_users)
        ]
        results = await asyncio.gather(*tasks)

        duration = time.monotonic() - batch_start
        failure_results = [(success, error) for success, error, _ in results if not success]
        user_timings = [timings for success, _, timings in results if success and timings is not None]

        print(f"\n--- Mocked LLM Load Test Completed in {duration:.2f}s ---")
        print(f"Total Users: {self.num_users}")
        print(f"Success: {self.num_users - len(failure_results)}")
        print(f"Failure: {len(failure_results)}")

        if failure_results:
            print("\n--- Failure Details ---")
            for i, (_, error) in enumerate(failure_results):
                print(f"Failure {i + 1}: {error}")

        if self.test_state_isolation and not failure_results:
            isolated, isolation_error = await self.validate_all_threads_isolated(thread_ids)
            if not isolated:
                failure_results.append((False, isolation_error))
                print(f"\n--- Post-run isolation failure ---\n{isolation_error}")

        self.assertEqual(
            len(failure_results),
            0,
            f"{len(failure_results)} users failed the test. Errors: {[e for _, e in failure_results]}",
        )

        if user_timings:
            report = LoadTestConcurrencyReport.build(
                num_users=self.num_users,
                wall_clock_s=duration,
                batch_start=batch_start,
                timings=user_timings,
            )
            print_load_test_report(report, title="Mocked LLM Load Test Concurrency Report")

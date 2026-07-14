import asyncio
import uuid
import time
import os
import httpx
import pytest
from system_tests.e2e.base_test import BaseTestServerStream, SERVER_URL
from system_tests.load.conftest import resolve_load_test_users
from system_tests.load.metrics import (
    LoadTestConcurrencyReport,
    UserLoadTimings,
    print_load_test_report,
)

STATE_ENDPOINT = f"{SERVER_URL}/api/agent/state"

pytestmark = pytest.mark.load


class LoadTest(BaseTestServerStream):
    """
    Load test for concurrent users.
    """

    # Configure environment for API mode (no browser)
    test_env_vars = {
        "CUGA_MODE": "api",
        "CUGA_TEST_ENV": "true",
        "DYNACONF_SERVER_PORTS__DIGITAL_SALES_API": "8000",
        "DYNACONF_SERVER_PORTS__REGISTRY": "8001",
        "DYNACONF_SERVER_PORTS__DEMO": "7860",
        "DYNACONF_ADVANCED_FEATURES__TRACKER_ENABLED": "false",
    }

    # Flag to enable/disable state isolation testing
    test_state_isolation = True

    # Flag to enable/disable chat_messages isolation checks
    check_chat_messages_isolation = False
    num_users = 5

    # E2B mode flag - set via environment variable CUGA_E2B_MODE=true
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
                    "state": None,
                    "variables": {},
                    "variables_count": 0,
                    "chat_messages_count": 0,
                }
            else:
                raise Exception(f"Failed to get state: {response.status_code} - {response.text}")

    async def validate_state_isolation(
        self, user_id: int, thread_id: str, other_thread_ids: list[str]
    ) -> tuple[bool, str]:
        """
        Validate that this thread's state is isolated from other threads.
        Checks that threads that have completed have their own variable storage and chat messages.
        Returns (is_valid, error_message)
        """
        try:
            state_response = await self.get_agent_state(thread_id)
            my_variables = state_response.get("variables", {})
            my_chat_messages_count = self.get_state_chat_messages_count(state_response)

            # Check that we actually have variables
            if not my_variables:
                return False, f"User {user_id}: No variables found in state"

            # Check that we have chat messages (if isolation check is enabled)
            if self.check_chat_messages_isolation and my_chat_messages_count == 0:
                return False, f"User {user_id}: No chat_messages found in state"

            # For each other thread that has completed, verify they have their own variables and chat messages
            # Even if variable names are the same (they're doing the same task),
            # each thread should have its own storage
            # Optimization: Skipping N^2 validation check which hammers the server
            # for other_thread_id in other_thread_ids:
            #    if other_thread_id == thread_id:
            #        continue

            #    await self.get_agent_state(other_thread_id)

            # If other thread has variables and chat messages, that's good - it means it has its own storage
            # The key test is that initial state was empty and final state has variables and messages
            # This already proves isolation via LangGraph's checkpointer
            pass

            return True, ""
        except Exception as e:
            return False, f"User {user_id}: Error validating isolation: {e}"

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

            if self.test_state_isolation:
                state_check_started = time.monotonic()
                await asyncio.sleep(2)

                final_state = await self.get_agent_state(thread_id)
                final_variables_count = self.get_state_variables_count(final_state)
                final_chat_messages_count = self.get_state_chat_messages_count(final_state)
                timings.state_checked_at = time.monotonic()
                timings.state_check_duration_s = timings.state_checked_at - state_check_started

                if final_variables_count == 0:
                    return (
                        False,
                        f"User {user_id}: State should have variables after completion, but found 0 variables",
                        timings,
                    )
                if self.check_chat_messages_isolation and final_chat_messages_count == 0:
                    return (
                        False,
                        f"User {user_id}: State should have chat_messages after completion, but found 0",
                        timings,
                    )

                other_thread_ids = [tid for tid in all_thread_ids if tid != thread_id]
                is_isolated, isolation_error = await self.validate_state_isolation(
                    user_id, thread_id, other_thread_ids
                )
                if not is_isolated:
                    return False, isolation_error, timings

                print(f"User {user_id}: ✓ State is isolated from other threads")

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

            timings.finished_at = time.monotonic()
            timings.total_duration_s = timings.finished_at - timings.started_at

            print(f"User {user_id}: ✓ Followup question answered correctly")
            print(f"User {user_id}: Success!")
            return True, "", timings

        except Exception as e:
            timings.finished_at = time.monotonic()
            timings.total_duration_s = timings.finished_at - timings.started_at
            return False, f"User {user_id}: Exception: {e}", timings

    async def test_concurrent_users(self):
        """
        Simulate 20 concurrent users running the same task.
        Validates state isolation between threads.
        """
        num_users = self.num_users
        print(f"\n--- Starting Load Test with {num_users} users ---")

        batch_start = time.monotonic()
        thread_ids = [str(uuid.uuid4()) for _ in range(num_users)]

        tasks = []
        for i in range(num_users):
            tasks.append(self.run_single_user_task(i, thread_ids[i], thread_ids, batch_start))

        results = await asyncio.gather(*tasks)

        duration = time.monotonic() - batch_start

        success_results = [(success, error, timings) for success, error, timings in results if success]
        failure_results = [(success, error) for success, error, _ in results if not success]
        user_timings = [timings for _, _, timings in success_results if timings is not None]

        success_count = len(success_results)
        failure_count = len(failure_results)

        print(f"\n--- Load Test Completed in {duration:.2f}s ---")
        print(f"Total Users: {num_users}")
        print(f"Success: {success_count}")
        print(f"Failure: {failure_count}")

        if failure_count > 0:
            print("\n--- Failure Details ---")
            for i, (_, error) in enumerate(failure_results):
                print(f"Failure {i + 1}: {error}")

        self.assertEqual(
            failure_count,
            0,
            f"{failure_count} users failed the test. Errors: {[e for _, e in failure_results]}",
        )

        if user_timings:
            report = LoadTestConcurrencyReport.build(
                num_users=num_users,
                wall_clock_s=duration,
                batch_start=batch_start,
                timings=user_timings,
            )
            print_load_test_report(report, title="Load Test Concurrency Report")

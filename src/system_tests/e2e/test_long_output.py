import pytest
import unittest
import re
from langchain_core.messages import HumanMessage
from cuga.backend.llm.models import LLMManager
from cuga.config import settings

pytestmark = pytest.mark.e2e


class TestLongOutput(unittest.IsolatedAsyncioTestCase):
    """
    Test class for verifying that LLM can generate long outputs (at least 1600 tokens).
    This tests that max_tokens is properly set and working using LLMManager directly.
    """

    async def test_long_llm_output(self):
        """Test that LLM can generate outputs of at least 1600 tokens."""
        # Get model configuration
        model_config = settings.agent.code.model.copy()

        # Verify max_tokens is set correctly (should be 16000 for Groq)
        max_tokens_config = getattr(model_config, 'max_tokens', None)
        self.assertIsNotNone(max_tokens_config, "max_tokens not found in model configuration")
        self.assertGreater(
            max_tokens_config, 1000, f"max_tokens too low: {max_tokens_config}, should be > 1000"
        )
        self.assertNotEqual(
            max_tokens_config,
            1000,
            "max_tokens is still set to default 1000 - this indicates the fix didn't work",
        )

        print(f"\n=== Testing Long Output with max_tokens={max_tokens_config} ===")

        # Initialize LLM manager and get model
        llm_manager = LLMManager()
        model = llm_manager.get_model(model_config)

        # Verify model has correct max_tokens set
        model_max_tokens = getattr(model, 'max_tokens', None)
        if model_max_tokens:
            print(f"Model max_tokens attribute: {model_max_tokens}")
            # Note: Some models may store this in model_kwargs instead

        # Create a prompt that should generate a very long response
        prompt = (
            "Write a comprehensive, detailed analysis of artificial intelligence, "
            "covering its history from the 1950s to present day, major breakthroughs, "
            "current state-of-the-art techniques, ethical considerations, future implications, "
            "and potential societal impacts. Include specific examples, technical details, "
            "and references to key researchers and organizations. Make this analysis "
            "as thorough and detailed as possible, aiming for at least 2000 words. "
            "Be very detailed and comprehensive in your response."
        )

        print("Sending prompt to LLM...")

        try:
            # Call the LLM directly
            messages = [HumanMessage(content=prompt)]
            response = await model.ainvoke(messages)

            # Extract the response text
            if hasattr(response, 'content'):
                answer_text = response.content
            else:
                answer_text = str(response)

            self.assertIsNotNone(answer_text, "Response is None")
            self.assertNotEqual(answer_text.strip(), "", "Response is empty")

            print(f"Response length: {len(answer_text)} characters")

            # Count approximate tokens
            # More accurate: count words (rough approximation)
            words = re.findall(r'\b\w+\b', answer_text)
            approx_tokens = len(words)

            # Also estimate based on characters (1 token ≈ 4 chars for English)
            char_based_estimate = len(answer_text) // 4

            print(f"Approximate token count (word-based): {approx_tokens}")
            print(f"Approximate token count (char-based): {char_based_estimate}")
            print(f"Acccurate token count: {response.response_metadata}")
            # Use the higher estimate to be conservative
            final_estimate = max(approx_tokens, char_based_estimate)

            # Assert that we have at least 1600 tokens worth of content
            self.assertGreaterEqual(
                final_estimate,
                1600,
                f"Response too short: {final_estimate} tokens (estimated), expected at least 1600. "
                f"This suggests max_tokens may not be set correctly. "
                f"Config max_tokens={max_tokens_config}, Model max_tokens={model_max_tokens}",
            )

            print(f"✅ Response meets minimum length requirement: {final_estimate} tokens (estimated)")

            # Check if response appears truncated
            truncated_indicators = [
                "...",
                "truncated",
                "cut off",
                "incomplete",
                "continues",
                "to be continued",
            ]

            lower_answer = answer_text.lower()
            has_truncation_indicator = any(
                indicator in lower_answer[-200:] for indicator in truncated_indicators
            )

            if has_truncation_indicator and final_estimate < 2000:
                print("⚠️  Response may be truncated (found truncation indicators)")
            else:
                print("✅ Response appears complete")

            # Print a sample of the response
            print("\n--- Response Sample (first 500 chars) ---")
            print(answer_text[:500] + "..." if len(answer_text) > 500 else answer_text)

        except Exception as e:
            self.fail(f"Test failed with exception: {e}")

    def test_max_tokens_from_config(self):
        """Test that max_tokens is correctly read from configuration."""
        # Get the current model configuration
        model_config = settings.agent.code.model

        # Verify max_tokens is set and is a reasonable value
        max_tokens = getattr(model_config, 'max_tokens', None)
        self.assertIsNotNone(max_tokens, "max_tokens not found in model configuration")
        self.assertGreater(max_tokens, 1000, f"max_tokens too low: {max_tokens}, should be > 1000")

        print(f"✅ Model configuration has max_tokens = {max_tokens}")

        # Verify it's not the default 1000 that was causing the issue
        self.assertNotEqual(
            max_tokens, 1000, "max_tokens is still set to default 1000 - this indicates the fix didn't work"
        )

        # Verify LLMManager extracts it correctly
        llm_manager = LLMManager()
        model_config_copy = model_config.copy()

        # This should not raise an assertion error
        try:
            llm_manager.get_model(model_config_copy)
            print(f"✅ LLMManager.get_model() successfully used max_tokens={max_tokens} from config")
        except AssertionError as e:
            if "max_tokens must be specified" in str(e):
                self.fail(f"LLMManager failed to extract max_tokens from config: {e}")
            raise


if __name__ == "__main__":
    unittest.main()

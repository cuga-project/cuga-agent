"""
Adapter to use CUGA's LLM (BaseChatModel) with ToolGuard's I_TG_LLM interface.

This module provides a bridge between CUGA's LangChain-based LLM system and
ToolGuard's example generation system, allowing any CUGA-configured model
(OpenAI, Groq, Azure, etc.) to be used with ToolGuard.
"""

from typing import Dict, List
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, BaseMessage
from loguru import logger
from toolguard.buildtime.llm.llm_base import LanguageModelBase


class CugaLLMAdapter(LanguageModelBase):
    """
    Adapter that wraps CUGA's BaseChatModel to work with ToolGuard's I_TG_LLM interface.
    
    This allows using any LangChain-compatible model (OpenAI, Groq, Azure, etc.) 
    with ToolGuard's example generation system. The adapter handles message format
    conversion between ToolGuard's dict-based format and LangChain's message objects.
    
    Inherits from LanguageModelBase which provides:
    - chat_json(): Automatic JSON extraction with retry logic
    - extract_json_from_string(): JSON parsing utilities
    
    Example:
        ```python
        from cuga.sdk import CugaAgent
        from cuga.backend.cuga_graph.policy.tool_guard.cuga_llm_adapter import CugaLLMAdapter
        
        # Create agent with any model
        agent = CugaAgent(tools=[my_tool])
        
        # Wrap the model for ToolGuard
        llm_adapter = CugaLLMAdapter(agent._model)
        
        # Use with ToolGuard
        messages = [
            {"role": "system", "content": "You are a helpful assistant"},
            {"role": "user", "content": "Generate examples"}
        ]
        response = await llm_adapter.generate(messages)
        
        # Or use chat_json for structured output
        json_response = await llm_adapter.chat_json(messages)
        ```
    
    Attributes:
        model: The underlying LangChain BaseChatModel instance
    """
    
    def __init__(self, model: BaseChatModel):
        """
        Initialize the adapter with a CUGA/LangChain model.
        
        Args:
            model: BaseChatModel instance (e.g., ChatOpenAI, ChatGroq, AzureChatOpenAI, etc.)
                   This is typically obtained from agent._model in CugaAgent.
        
        Raises:
            TypeError: If model is not a BaseChatModel instance
        """
        if not isinstance(model, BaseChatModel):
            raise TypeError(
                f"Expected BaseChatModel instance, got {type(model).__name__}. "
                "Please pass agent._model from a CugaAgent instance."
            )
        
        self.model = model
        logger.info(f"Initialized CugaLLMAdapter with model: {type(model).__name__}")
    
    def _convert_to_langchain_messages(self, messages: List[Dict]) -> List[BaseMessage]:
        """
        Convert ToolGuard message format to LangChain message objects.
        
        ToolGuard uses a simple dict format:
            [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Hi"}]
        
        LangChain uses typed message objects:
            [HumanMessage(content="Hello"), AIMessage(content="Hi")]
        
        Args:
            messages: List of dicts with 'role' and 'content' keys.
                     Supported roles: 'system', 'user', 'human', 'assistant', 'ai'
            
        Returns:
            List of LangChain message objects (SystemMessage, HumanMessage, AIMessage)
        
        Raises:
            ValueError: If a message is missing 'role' or 'content' keys
        """
        langchain_messages = []
        
        for i, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise ValueError(
                    f"Message at index {i} must be a dict, got {type(msg).__name__}"
                )
            
            if "role" not in msg:
                raise ValueError(f"Message at index {i} missing 'role' key: {msg}")
            
            if "content" not in msg:
                raise ValueError(f"Message at index {i} missing 'content' key: {msg}")
            
            role = msg["role"].lower()
            content = msg["content"]
            
            # Convert to appropriate LangChain message type
            if role == "system":
                langchain_messages.append(SystemMessage(content=content))
            elif role in ("assistant", "ai"):
                langchain_messages.append(AIMessage(content=content))
            elif role in ("user", "human"):
                langchain_messages.append(HumanMessage(content=content))
            else:
                logger.warning(
                    f"Unknown role '{role}' at index {i}, treating as user message"
                )
                langchain_messages.append(HumanMessage(content=content))
        
        return langchain_messages
    
    async def generate(self, messages: List[Dict]) -> str:
        """
        Generate a text response from the model.
        
        This is the core method required by I_TG_LLM interface. It converts
        ToolGuard's message format to LangChain format, invokes the model,
        and returns the generated text.
        
        Args:
            messages: List of message dicts in format:
                [
                    {"role": "system", "content": "You are a helpful assistant"},
                    {"role": "user", "content": "Generate examples"},
                    {"role": "assistant", "content": "Here are examples..."}
                ]
        
        Returns:
            Generated text response as string
        
        Raises:
            ValueError: If messages format is invalid
            Exception: If model invocation fails (propagated from LangChain)
        
        Example:
            ```python
            adapter = CugaLLMAdapter(model)
            messages = [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Say hello"}
            ]
            response = await adapter.generate(messages)
            print(response)  # "Hello! How can I help you today?"
            ```
        """
        try:
            # Convert to LangChain format
            langchain_messages = self._convert_to_langchain_messages(messages)
            
            logger.debug(
                f"Invoking {type(self.model).__name__} with {len(langchain_messages)} messages"
            )
            
            # Invoke the model using LangChain's async interface
            response = await self.model.ainvoke(langchain_messages)
            
            # Extract content from response
            # LangChain models return AIMessage or similar with .content attribute
            if hasattr(response, 'content'):
                content = response.content
                # Handle case where content might be a list (e.g., multimodal responses)
                if isinstance(content, list):
                    # Join list items into a single string
                    content = "\n".join(str(item) for item in content)
                elif not isinstance(content, str):
                    content = str(content)
            else:
                content = str(response)
            
            logger.debug(f"Generated response length: {len(content)} characters")
            
            return content
            
        except ValueError as e:
            # Re-raise validation errors
            logger.error(f"Message format error: {e}")
            raise
        except Exception as e:
            # Log and re-raise model invocation errors
            logger.error(f"Error generating response with {type(self.model).__name__}: {e}")
            raise RuntimeError(
                f"Failed to generate response: {e}. "
                "Check that your model is properly configured and has valid credentials."
            ) from e

# Made with Bob

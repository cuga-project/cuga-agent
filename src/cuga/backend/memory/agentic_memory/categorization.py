"""
Memory Categorization System - Phase 1: Core Logic + Predefined Mode

This module implements the category management system for memory facts,
supporting predefined categorization mode as the first phase of implementation.
"""

from typing import Literal
from cuga.config import settings


class CategoryManager:
    """
    Manages memory fact categorization with support for predefined categories.

    Phase 1 Implementation: Predefined mode only
    Future phases will add dynamic and hybrid modes.
    """

    # Predefined categories with descriptions
    PREDEFINED_CATEGORIES = {
        "personal_details": "User's personal information (name, age, location, etc.)",
        "family": "Family members and relationships",
        "professional_details": "Work, career, job-related information",
        "sports": "Sports activities, teams, fitness",
        "travel": "Travel plans, destinations, preferences",
        "food": "Food preferences, dietary restrictions, favorite cuisines",
        "music": "Music preferences, favorite artists, instruments",
        "health": "Health information, medical details, wellness",
        "technology": "Tech preferences, devices, software",
        "hobbies": "Hobbies and leisure activities",
        "fashion": "Fashion preferences, style, clothing",
        "entertainment": "Movies, TV shows, books, games",
        "milestones": "Important life events, achievements",
        "user_preferences": "General preferences and settings",
        "misc": "Anything that doesn't fit other categories",
    }

    def __init__(
        self,
        mode: Literal["predefined", "dynamic", "hybrid"] | None = None,
        allow_dynamic_categories: bool | None = None,
        confirm_new_categories: bool | None = None,
    ):
        """
        Initialize the CategoryManager.

        Args:
            mode: Categorization mode. Defaults to settings.memory.categorization_mode
            allow_dynamic_categories: Allow LLM to create new categories.
                                     Defaults to settings.memory.allow_dynamic_categories
            confirm_new_categories: Require user confirmation for new categories.
                                   Defaults to settings.memory.confirm_new_categories
        """
        # Load from settings if not provided
        self.mode = mode or getattr(settings.memory, 'categorization_mode', 'predefined')
        self.allow_dynamic_categories = (
            allow_dynamic_categories
            if allow_dynamic_categories is not None
            else getattr(settings.memory, 'allow_dynamic_categories', False)
        )
        self.confirm_new_categories = (
            confirm_new_categories
            if confirm_new_categories is not None
            else getattr(settings.memory, 'confirm_new_categories', False)
        )

        # Track custom categories discovered during runtime
        self.custom_categories: set[str] = set()

        # Validate mode
        if self.mode not in ["predefined", "dynamic", "hybrid"]:
            raise ValueError(
                f"Invalid categorization mode: {self.mode}. Must be 'predefined', 'dynamic', or 'hybrid'"
            )

    @property
    def predefined_categories(self) -> list[str]:
        """Get list of predefined category names."""
        return list(self.PREDEFINED_CATEGORIES.keys())

    def get_category_description(self, category: str) -> str | None:
        """
        Get the description for a category.

        Args:
            category: Category name

        Returns:
            Category description or None if not found
        """
        return self.PREDEFINED_CATEGORIES.get(category)

    def get_available_categories(self) -> dict:
        """
        Get categories available for LLM to use based on current mode.

        Returns:
            Dictionary with categorization information for prompt building
        """
        if self.mode == "predefined":
            return {
                "type": "predefined_only",
                "categories": self.predefined_categories,
                "descriptions": self.PREDEFINED_CATEGORIES,
            }
        elif self.mode == "dynamic":
            return {"type": "dynamic", "existing_categories": list(self.custom_categories)}
        else:  # hybrid
            return {
                "type": "hybrid",
                "predefined": self.predefined_categories,
                "descriptions": self.PREDEFINED_CATEGORIES,
                "custom": list(self.custom_categories),
            }

    def register_category(self, category: str) -> None:
        """
        Register a newly discovered category.

        Args:
            category: Category name to register
        """
        if category not in self.PREDEFINED_CATEGORIES:
            self.custom_categories.add(category)

    def validate_category(self, category: str) -> bool:
        """
        Check if category is valid for current mode.

        Args:
            category: Category name to validate

        Returns:
            True if category is valid, False otherwise
        """
        if self.mode == "predefined":
            return category in self.PREDEFINED_CATEGORIES
        else:
            # Dynamic and hybrid modes allow any category
            return True

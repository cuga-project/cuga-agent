from dynaconf import Dynaconf
from pathlib import Path
from loguru import logger
from cuga.backend.activity_tracker.tracker import ActivityTracker
from cuga.backend.cuga_graph.utils.nodes_names import NodeNames
from cuga.configurations.set_from_one_file import parse_markdown_sections
from cuga.config import settings

root_dir = Path(__file__).parent.parent.absolute()

tracker = ActivityTracker()


class InstructionsManager:
    """Singleton class for managing instructions configuration"""

    _instance = None
    _in_memory_cache = {}

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super(InstructionsManager, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        # Only initialize once
        if not hasattr(self, '_initialized'):
            self._load_configuration()
            self._setup_key_mappings()
            self._log_initialization_summary()
            self._initialized = True

    def _setup_key_mappings(self):
        """Setup hard-coded key mappings for alternative access"""
        # Hard-coded mapping from alternative names to actual keys
        self._key_mappings = {
            NodeNames.FINAL_ANSWER_AGENT: "answer",
            "api_code_planner": "api_code_planner",
            "plan_controller": "plan_controller",
            "task_decomposition": "task_decomposition",
            "api_planner": "api_planner",
            "shortlister": "shortlister",
            "code_agent": "code_agent",
        }

        # You can also create reverse mappings if needed
        self._reverse_mappings = {v: k for k, v in self._key_mappings.items()}

    def _resolve_key(self, key):
        """Resolve a key through the mapping system"""
        # First check if it's a direct key
        if key in self._instructions:
            return key

        # Then check if it's an alternative name
        if key in self._key_mappings:
            mapped_key = self._key_mappings[key]
            if mapped_key in self._instructions:
                return mapped_key
            else:
                logger.warning(f"Mapped key '{mapped_key}' for alias '{key}' not found in instructions")
                return None

        # Key not found in either direct keys or mappings
        return None

    def _load_configuration(self):
        """Load configuration once during initialization"""
        # Initialize dynaconf with TOML support (dotenv disabled)
        self._instructions_settings = Dynaconf(
            envvar_prefix="TOML_TEST",
            settings_files=["./configurations/instructions/instructions.toml"],
            environments=True,
            root_path=root_dir,
            load_dotenv=False,  # Disable dotenv loading
            core_loaders=["TOML"],
        )

        # Load TOML data directly
        self._instructions = self._load_toml_config()

    def _load_toml_config(self):
        """Load TOML configuration using Dynaconf"""
        try:
            config_path = root_dir / Path("./configurations/instructions")

            settings = Dynaconf(
                settings_files=[str(config_path / "instructions.toml")],
                load_dotenv=False,  # Optional: also load .env files
                envvar_prefix="INSTRUCTIONS",  # Optional: prefix for env vars
            )

            return settings

        except Exception as e:
            logger.error(f"Error loading TOML configuration: {e}")
            return Dynaconf()  # Return empty Dynaconf instance

    def _log_initialization_summary(self):
        """Log a nice summary of what configuration was loaded"""
        try:
            config_path = root_dir / Path("./configurations/instructions/instructions.toml")

            # Basic file info
            if config_path.exists():
                file_size = config_path.stat().st_size
                logger.success("📋 Instructions configuration loaded successfully")
                logger.info(f"   📁 Config file: {config_path.relative_to(root_dir)}")
                logger.info(f"   📏 File size: {file_size:,} bytes")
            else:
                logger.warning(f"   ⚠️  Config file not found: {config_path}")
                return

            # Count instruction keys
            instruction_keys = self.get_all_instruction_keys()
            logger.info(f"   🔑 Instruction sections: {len(instruction_keys)}")

            # Log key mappings info
            if self._key_mappings:
                logger.info(f"   🔗 Key mappings available: {len(self._key_mappings)}")
                if len(self._key_mappings) <= 5:
                    mapping_str = ", ".join([f"{k}→{v}" for k, v in self._key_mappings.items()])
                    logger.info(f"   🏷️  Mappings: {mapping_str}")
                else:
                    sample_mappings = dict(list(self._key_mappings.items())[:3])
                    mapping_str = ", ".join([f"{k}→{v}" for k, v in sample_mappings.items()])
                    logger.info(
                        f"   🏷️  Sample mappings: {mapping_str}... (+{len(self._key_mappings) - 3} more)"
                    )

            if instruction_keys:
                # Count file-based vs inline instructions
                file_based_count = 0
                inline_count = 0
                total_chars = 0

                for key in instruction_keys:
                    raw_value = self._instructions.get(key, {}).get('instructions', "")
                    if raw_value.startswith("./"):
                        file_based_count += 1
                        # Try to get actual content length
                        content = self._load_file_content(raw_value)
                        total_chars += len(content)
                    else:
                        inline_count += 1
                        total_chars += len(raw_value)

                logger.info(f"   📝 Inline instructions: {inline_count}")
                logger.info(f"   📄 File-based instructions: {file_based_count}")
                logger.info(f"   📊 Total content length: {total_chars:,} characters")

                # Show available instruction keys (limited to avoid spam)
                if len(instruction_keys) <= 10:
                    keys_str = ", ".join(instruction_keys)
                    logger.info(f"   🏷️  Available keys: {keys_str}")
                else:
                    sample_keys = instruction_keys[:8]
                    keys_str = ", ".join(sample_keys)
                    logger.info(f"   🏷️  Available keys: {keys_str}... (+{len(instruction_keys) - 8} more)")
            else:
                logger.warning("   ⚠️  No instruction sections found in configuration")

        except Exception as e:
            logger.error(f"Error generating initialization summary: {e}")

    def _load_file_content(self, file_path):
        """Load file content if it exists, return empty string otherwise"""
        # Handle relative paths starting with ./
        if file_path.startswith("./"):
            file_path = file_path[2:]

        # Get the absolute path of the current config.py file
        config_dir = Path(__file__).parent.parent.absolute()
        full_path = config_dir / file_path

        # Validate that the path is within the config directory for security
        try:
            full_path = full_path.resolve()
            if not str(full_path).startswith(str(config_dir)):
                logger.warning(f"Security warning: Path {file_path} is outside config directory")
                return ""
        except Exception as e:
            logger.error(f"Error resolving path {file_path}: {e}")
            return ""

        if full_path.exists():
            try:
                with open(full_path, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except Exception as e:
                logger.error(f"Error reading file {full_path}: {e}")
                return ""
        else:
            logger.warning(f"File not found: {full_path}")
            return ""

    def get_instructions(self, key):
        """
        Generic function to get instructions for any key.
        Checks in-memory cache first, then configuration.
        """
        resolved_key = self._resolve_key(key)

        if resolved_key is None:
            logger.warning(f"Key '{key}' not found in instructions or key mappings")
            return ""

        # Check cache with both original and uppercase key
        cache_key = resolved_key.upper() if resolved_key else None
        if cache_key and cache_key in self._in_memory_cache:
            logger.info(f"Loaded '{cache_key}' from in-memory cache.")
            return self._in_memory_cache[cache_key]
        elif resolved_key in self._in_memory_cache:
            logger.info(f"Loaded '{resolved_key}' from in-memory cache.")
            return self._in_memory_cache[resolved_key]

        try:
            # Log if we used a mapping
            if resolved_key != key:
                logger.debug(f"Using key mapping: '{key}' → '{resolved_key}'")

            value = self._instructions.get(resolved_key, {}).get('instructions', "")
            if value.startswith("./"):
                content = self._load_file_content(value)
            else:
                content = value

            # Store in cache with uppercase key for consistency
            if cache_key:
                self._in_memory_cache[cache_key] = content
            return content
        except Exception as e:
            logger.error(f"Error getting instructions for key '{key}': {e}")
            return ""

    def set_instructions_from_one_file(self, instructions: str | None = None):
        if not instructions:
            self._in_memory_cache.clear()
            return

        res = parse_markdown_sections(instructions)
        if res.personal_information:
            tracker.pi = res.personal_information
        if res.answer:
            resolved_key = self._resolve_key('answer')
            # Normalize to uppercase to match get_all_instruction_keys() output
            if resolved_key:
                self._in_memory_cache[resolved_key.upper()] = res.answer
        if res.plan:
            resolved_key = self._resolve_key('api_planner')
            if resolved_key:
                self._in_memory_cache[resolved_key.upper()] = res.plan

    def set_instruction(self, key_name: str, value: str):
        """
        Sets or updates an instruction in the in-memory cache.
        This will override any instruction loaded from configuration.
        """
        resolved_key = self._resolve_key(key_name)
        if resolved_key is None:
            # If key doesn't exist, we can't set it unless we add it to instructions.
            # For this implementation, we will add it to the cache directly.
            resolved_key = key_name
            logger.warning(f"Key '{key_name}' not found in configuration. Adding to in-memory cache only.")

        # Use uppercase key for consistency
        cache_key = resolved_key.upper() if resolved_key else resolved_key
        self._in_memory_cache[cache_key] = value
        logger.info(f"Set instruction for key '{cache_key}' in memory.")

    def get_all_instruction_keys(self):
        """Get all keys that have 'instructions' as a child"""
        instruction_keys = []
        for key, value in self._instructions.items():
            if isinstance(value, dict) and 'instructions' in value:
                instruction_keys.append(key)
        return instruction_keys

    def get_all_available_keys(self):
        """Get all available keys including both direct keys and mapped aliases"""
        direct_keys = self.get_all_instruction_keys()
        mapped_keys = list(self._key_mappings.keys())
        return {'direct_keys': direct_keys, 'mapped_keys': mapped_keys, 'all_keys': direct_keys + mapped_keys}

    def get_all_instructions_formatted(self):
        """Get all instructions formatted as markdown with key-value pairs"""
        instruction_keys = self.get_all_instruction_keys()
        if not instruction_keys:
            logger.warning("No instruction keys found")
            return None

        markdown_sections = []
        for key in sorted(instruction_keys):
            instructions = self.get_instructions(key)
            if instructions.strip():
                # Format as nested bullet points under the key
                formatted_key = key.replace('_', ' ').title()
                # Format instructions content as nested bullets if multi-line
                instruction_lines = instructions.strip().split('\n')
                if len(instruction_lines) > 1:
                    # Multi-line: format each line as nested bullet
                    nested_content = '\n'.join(
                        f"  - {line.strip()}" for line in instruction_lines if line.strip()
                    )
                    section = f"- **{formatted_key}**\n{nested_content}"
                else:
                    # Single line: simple format
                    section = f"- **{formatted_key}**\n  - {instructions.strip()}"
                markdown_sections.append(section)

        # Return None if no sections were added (all values were empty)
        if not markdown_sections:
            logger.warning("No markdown sections found")
            return None
        logger.info(f"All instructions formatted: {markdown_sections}")
        return "\n\n".join(markdown_sections)

    def get_key_mappings(self):
        """Get the current key mappings dictionary"""
        return self._key_mappings.copy()

    def add_key_mapping(self, alias, actual_key):
        """Add a new key mapping at runtime"""
        if actual_key not in self._instructions:
            logger.warning(f"Target key '{actual_key}' does not exist in instructions")
            return False

        self._key_mappings[alias] = actual_key
        logger.info(f"Added key mapping: '{alias}' → '{actual_key}'")
        return True

    def remove_key_mapping(self, alias):
        """Remove a key mapping"""
        if alias in self._key_mappings:
            removed_key = self._key_mappings.pop(alias)
            logger.info(f"Removed key mapping: '{alias}' → '{removed_key}'")
            return True
        else:
            logger.warning(f"Key mapping '{alias}' not found")
            return False

    def reload_configuration(self):
        """Manually reload configuration if needed"""
        logger.info("🔄 Reloading instructions configuration...")
        self._load_configuration()
        self._setup_key_mappings()  # Reload mappings as well
        self._log_initialization_summary()
        logger.success("✅ Configuration reloaded successfully")

    @property
    def instructions_settings(self):
        """Access to the dynaconf settings object"""
        return self._instructions_settings

    @property
    def raw_instructions(self):
        """Access to the raw instructions dictionary"""
        return self._instructions

    @property
    def key_mappings(self):
        """Access to the key mappings dictionary"""
        return self._key_mappings.copy()


# Convenience functions for backward compatibility
def get_instructions_manager():
    """Get the singleton instance"""
    return InstructionsManager()


def get_instructions(key):
    """Get instructions for a key using the singleton"""
    return get_instructions_manager().get_instructions(key)


def get_all_instruction_keys():
    """Get all instruction keys using the singleton"""
    return get_instructions_manager().get_all_instruction_keys()


def get_all_available_keys():
    """Get all available keys including mapped aliases"""
    return get_instructions_manager().get_all_available_keys()


def add_key_mapping(alias, actual_key):
    """Add a new key mapping"""
    return get_instructions_manager().add_key_mapping(alias, actual_key)


def get_all_instructions_formatted():
    """Get all instructions formatted as markdown"""
    return get_instructions_manager().get_all_instructions_formatted()

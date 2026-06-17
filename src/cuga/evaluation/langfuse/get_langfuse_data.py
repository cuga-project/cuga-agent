import asyncio
from dataclasses import dataclass
from typing import Optional, Dict, Any, List
import httpx
import os


@dataclass
class Config:
    langfuse_public_key: str
    langfuse_secret_key: str
    langfuse_host: str


@dataclass
class LangfuseMetrics:
    """Data class to store extracted Langfuse metrics"""

    trace_id: str
    total_llm_calls: int
    total_tokens: int
    total_cost: float
    node_timings: Dict[str, float]
    llm_call_details: List[Dict[str, Any]]
    total_generation_time: float  # Total time spent on all GENERATION events
    generation_timings: List[Dict[str, Any]]  # Sorted list of generations by time spent
    full_execution_time: float  # Full execution time from trace
    total_cache_input_tokens: int


class LangfuseTraceHandler:
    def __init__(self, trace_id: str):
        self.trace_id = trace_id
        langfuse_public_key = os.getenv('LANGFUSE_PUBLIC_KEY', None)
        langfuse_secret_key = os.getenv('LANGFUSE_SECRET_KEY', None)
        langfuse_host = os.getenv('LANGFUSE_HOST', 'https://cloud.langfuse.com')
        if not langfuse_public_key or not langfuse_secret_key:
            print("Error: Langfuse host or secret key not set, make sure to add them in your .env file")
        self.config = Config(langfuse_public_key, langfuse_secret_key, langfuse_host)

    async def get_langfuse_data(self) -> LangfuseMetrics:
        if not self.trace_id:
            print("No Langfuse trace ID, cannot get data")
            return None
        # Skip the retry loop if credentials weren't provided at construction
        # time. Without this guard, httpx fails to build a Basic auth header
        # from (None, None) and the per-trace retry sleep loop wastes ~150s
        # per task (2 + 3 + 4.5 + ... over 10 attempts). The warning was
        # already printed once in __init__. See issue #318.
        if not self.config.langfuse_public_key or not self.config.langfuse_secret_key:
            return None
        print(f"Fetching Langfuse data for trace {self.trace_id}...")
        langfuse_data = await self.extract_langfuse_data(
            self.config,
            self.trace_id,
            max_retries=10,
            initial_delay=2.0,
        )
        if not langfuse_data:
            print("⚠ Could not retrieve complete Langfuse data")
            return None
        parsed_data = self.parse_langfuse_metrics(langfuse_data)
        return parsed_data

    @staticmethod
    async def extract_langfuse_data(
        config, trace_id: str, max_retries: int = 10, initial_delay: float = 2.0
    ) -> Optional[Dict[str, Any]]:
        """
        Extract data from Langfuse API with retry logic.

        Langfuse data takes time to propagate to the server, so we retry with exponential backoff.

        Args:
            trace_id: The Langfuse trace ID to fetch
            max_retries: Maximum number of retry attempts (default: 10)
            initial_delay: Initial delay in seconds before first retry (default: 2.0)
        """
        auth = (config.langfuse_public_key, config.langfuse_secret_key)
        url = f"{config.langfuse_host}/api/public/traces/{trace_id}"

        delay = initial_delay

        for attempt in range(max_retries):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    response = await client.get(url, auth=auth)

                    if response.status_code == 404:
                        if attempt < max_retries - 1:
                            print(
                                f"  Trace not yet available (attempt {attempt + 1}/{max_retries}), waiting {delay:.1f}s..."
                            )
                            await asyncio.sleep(delay)
                            delay *= 1.5
                            continue
                        else:
                            print(f"  Warning: Trace {trace_id} not found after {max_retries} attempts")
                            return None

                    response.raise_for_status()
                    data = response.json()

                    if not data.get('observations') or len(data.get('observations', [])) == 0:
                        if attempt < max_retries - 1:
                            print(
                                f"  Trace data incomplete (no observations yet, attempt {attempt + 1}/{max_retries}), waiting {delay:.1f}s..."
                            )
                            await asyncio.sleep(delay)
                            delay *= 1.5
                            continue
                        else:
                            print(f"  Warning: Trace data still incomplete after {max_retries} attempts")
                            return data

                    print(
                        f"  ✓ Langfuse data fetched successfully ({len(data.get('observations', []))} observations)"
                    )
                    return data

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 404:
                    if attempt < max_retries - 1:
                        print(
                            f"  Trace not yet available (attempt {attempt + 1}/{max_retries}), waiting {delay:.1f}s..."
                        )
                        await asyncio.sleep(delay)
                        delay *= 1.5
                        continue
                    else:
                        print(f"  Warning: Trace {trace_id} not found after {max_retries} attempts")
                        return None
                else:
                    print(f"  Warning: HTTP error fetching Langfuse data: {e}")
                    return None

            except Exception as e:
                if attempt < max_retries - 1:
                    print(
                        f"  Error fetching data (attempt {attempt + 1}/{max_retries}): {e}, retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
                    delay *= 1.5
                    continue
                else:
                    print(
                        f"  Warning: Could not fetch Langfuse data for trace {trace_id} after {max_retries} attempts: {e}"
                    )
                    return None

        return None

    @staticmethod
    def parse_langfuse_metrics(langfuse_data: Dict[str, Any]) -> LangfuseMetrics:
        def _find_generation_events_recursive(
            data: Any, generations: List[Dict[str, Any]] = None
        ) -> List[Dict[str, Any]]:
            """Recursively find all GENERATION events in Langfuse data"""
            if generations is None:
                generations = []

            if isinstance(data, dict):
                # Check if this is a GENERATION event
                if data.get('type') == 'GENERATION':
                    generations.append(data)

                # Recursively search all values in the dictionary
                for value in data.values():
                    _find_generation_events_recursive(value, generations)
            elif isinstance(data, list):
                # Recursively search all items in the list
                for item in data:
                    _find_generation_events_recursive(item, generations)

            return generations

        """Parse Langfuse data to extract useful metrics"""
        if not langfuse_data:
            return None

        # Extract basic trace information
        trace_id = langfuse_data.get('id', 'unknown')

        # Find all GENERATION events recursively
        all_generations = _find_generation_events_recursive(langfuse_data)

        # Count LLM calls and extract details
        llm_calls = []
        total_tokens = 0
        total_cost = 0.0
        total_cache_input_tokens = 0
        total_generation_time = 0.0

        # Process all GENERATION events
        for gen in all_generations:
            # Prefer explicit duration; if missing/zero, compute from timestamps
            duration = gen.get('duration', 0) or 0
            if (not duration) and gen.get('startTime') and gen.get('endTime'):
                try:
                    from datetime import datetime

                    start_time_dt = datetime.fromisoformat(gen['startTime'].replace('Z', '+00:00'))
                    end_time_dt = datetime.fromisoformat(gen['endTime'].replace('Z', '+00:00'))
                    duration = int((end_time_dt - start_time_dt).total_seconds() * 1000)
                except Exception:
                    duration = 0

            total_generation_time += duration
            if 'costDetails' in gen:
                cost = gen.get('costDetails', {}).get('total', 0.0)
            else:
                cost = gen.get('usage', {}).get('totalCost', 0.0)
            llm_calls.append(
                {
                    'model': gen.get('model', 'unknown'),
                    'tokens': gen.get('usage', {}).get('total', 0),
                    'cache_input_tokens': gen.get('usage', {}).get('input_cache_read', 0),
                    'cost': cost,
                    'duration': duration,
                    'langgraph_node': gen.get('metadata', {}).get('langgraph_node', 'unknown'),
                    'start_time': gen.get('startTime', ''),
                    'end_time': gen.get('endTime', ''),
                    'id': gen.get('id', ''),
                }
            )
            total_tokens += gen.get('usage', {}).get('total', 0)
            total_cache_input_tokens += gen.get('usage', {}).get('input_cache_read', 0)
            total_cost += cost

        # Create generation timings sorted by duration (longest first)
        generation_timings = []
        for gen in all_generations:
            # Recompute duration the same way to ensure consistency
            duration = gen.get('duration', 0) or 0
            if (not duration) and gen.get('startTime') and gen.get('endTime'):
                try:
                    from datetime import datetime

                    start_time_dt = datetime.fromisoformat(gen['startTime'].replace('Z', '+00:00'))
                    end_time_dt = datetime.fromisoformat(gen['endTime'].replace('Z', '+00:00'))
                    duration = int((end_time_dt - start_time_dt).total_seconds() * 1000)
                except Exception:
                    duration = 0

            langgraph_node = gen.get('metadata', {}).get('langgraph_node', 'unknown')
            generation_timings.append(
                {
                    'langgraph_node': langgraph_node,
                    'duration': duration,
                    'duration_seconds': duration / 1000 if duration else 0.0,  # Convert to seconds
                    'model': gen.get('model', 'unknown'),
                    'tokens': gen.get('usage', {}).get('total', 0),
                    'cost': gen.get('usage', {}).get('totalCost', 0.0),
                    'start_time': gen.get('startTime', ''),
                    'end_time': gen.get('endTime', ''),
                    'id': gen.get('id', ''),
                }
            )

        # Sort by duration (longest first)
        generation_timings.sort(key=lambda x: x['duration'], reverse=True)

        # Extract node timings
        node_timings = {}
        spans = langfuse_data.get('spans', [])
        for span in spans:
            name = span.get('name', 'unknown')
            duration = span.get('duration', 0)
            if duration > 0:
                node_timings[name] = duration / 1000  # Convert to seconds

        # Calculate full execution time as the UNION of all observation intervals (no double counting overlaps)
        # Build intervals from observations' startTime/endTime and merge them
        full_execution_time = 0.0
        try:
            from datetime import datetime

            observations = langfuse_data.get('observations', []) or []
            intervals = []
            for obs in observations:
                start_ts = obs.get('startTime')
                end_ts = obs.get('endTime')
                if not start_ts or not end_ts:
                    continue
                try:
                    start_dt = datetime.fromisoformat(str(start_ts).replace('Z', '+00:00'))
                    end_dt = datetime.fromisoformat(str(end_ts).replace('Z', '+00:00'))
                    if end_dt <= start_dt:
                        continue
                    intervals.append((start_dt.timestamp(), end_dt.timestamp()))
                except Exception:
                    continue

            if intervals:
                intervals.sort(key=lambda x: x[0])
                merged = []
                cur_start, cur_end = intervals[0]
                for s, e in intervals[1:]:
                    if s <= cur_end:
                        if e > cur_end:
                            cur_end = e
                    else:
                        merged.append((cur_start, cur_end))
                        cur_start, cur_end = s, e
                merged.append((cur_start, cur_end))

                for s, e in merged:
                    full_execution_time += e - s

            # Fallbacks if no intervals merged
            if full_execution_time == 0.0:
                latency = langfuse_data.get('latency')
                if isinstance(latency, (int, float)) and latency > 0:
                    full_execution_time = float(latency)
                elif 'startTime' in langfuse_data and 'endTime' in langfuse_data:
                    try:
                        start_time = datetime.fromisoformat(langfuse_data['startTime'].replace('Z', '+00:00'))
                        end_time = datetime.fromisoformat(langfuse_data['endTime'].replace('Z', '+00:00'))
                        full_execution_time = (end_time - start_time).total_seconds()
                    except Exception as e:
                        print(f"Warning: Could not parse execution time: {e}")
                        full_execution_time = langfuse_data.get('duration', 0) / 1000.0
        except Exception as e:
            print(f"Warning: Failed to compute full_execution_time from observations: {e}")
            latency = langfuse_data.get('latency')
            if isinstance(latency, (int, float)) and latency > 0:
                full_execution_time = float(latency)
            else:
                full_execution_time = langfuse_data.get('duration', 0) / 1000.0

        return LangfuseMetrics(
            trace_id=trace_id,
            total_llm_calls=len(llm_calls),
            total_tokens=total_tokens,
            total_cost=total_cost,
            node_timings=node_timings,
            llm_call_details=llm_calls,
            total_generation_time=total_generation_time / 1000,  # Convert to seconds
            generation_timings=generation_timings,
            full_execution_time=full_execution_time,
            total_cache_input_tokens=total_cache_input_tokens,
        )

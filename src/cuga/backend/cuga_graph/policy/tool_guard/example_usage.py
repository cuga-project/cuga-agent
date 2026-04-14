"""
Example usage of the CUGA to Toolguard integration.

This file demonstrates how to convert CUGA tools to Toolguard's OpenAPI format
and then to ToolInfo objects for policy enforcement.

Includes examples of:
- Simple return types (str, int)
- Complex return types (Pydantic models, nested dicts, lists of objects)
- How OpenAPI specs reflect these complex structures
"""

import asyncio
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime

from loguru import logger
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from cuga.backend.cuga_graph.nodes.cuga_lite.direct_langchain_tools_provider import DirectLangChainToolsProvider
from cuga.backend.cuga_graph.policy.tool_guard import cuga_tools_to_oas


# ============================================================================
# Complex Object Models (Pydantic)
# ============================================================================

class ContactInfo(BaseModel):
    """Contact information model"""
    id: int = Field(description="Unique contact identifier")
    name: str = Field(description="Full name of the contact")
    email: str = Field(description="Email address")
    phone: str | None = Field(None, description="Optional phone number")
    company: str | None = Field(None, description="Company name")
    tags: List[str] = Field(default_factory=list, description="Contact tags")


class EmailMetadata(BaseModel):
    """Email metadata model"""
    sent_at: str = Field(description="Timestamp when email was sent")
    message_id: str = Field(description="Unique message identifier")
    status: str = Field(description="Delivery status")


class EmailResult(BaseModel):
    """Email sending result with metadata"""
    success: bool = Field(description="Whether email was sent successfully")
    recipient: str = Field(description="Email recipient")
    subject: str = Field(description="Email subject")
    metadata: EmailMetadata = Field(description="Email metadata")


class WeatherData(BaseModel):
    """Detailed weather information"""
    location: str = Field(description="Location name")
    temperature: float = Field(description="Current temperature")
    units: str = Field(description="Temperature units (celsius/fahrenheit)")
    conditions: str = Field(description="Weather conditions")
    humidity: int = Field(description="Humidity percentage")
    wind_speed: float = Field(description="Wind speed")
    forecast: List[Dict[str, Any]] = Field(description="3-day forecast")


class SearchResult(BaseModel):
    """Search result with pagination"""
    query: str = Field(description="Search query used")
    total_results: int = Field(description="Total number of results")
    page: int = Field(description="Current page number")
    results: List[ContactInfo] = Field(description="List of matching contacts")
    has_more: bool = Field(description="Whether more results are available")


# ============================================================================
# Simple Tools (String Return Types)
# ============================================================================

@tool
def calculate(expression: str) -> str:
    """
    Evaluate a mathematical expression.
    
    Args:
        expression: Mathematical expression to evaluate (e.g., "2 + 2", "10 * 5")
    
    Returns:
        Result of the calculation
    """
    try:
        # Simple eval for demo - in production use a safe math parser
        result = eval(expression, {"__builtins__": {}}, {})
        return f"Result: {result}"
    except Exception as e:
        return f"Error: {str(e)}"


# ============================================================================
# Complex Tools (Pydantic Model Return Types)
# ============================================================================

@tool
def search_contacts_advanced(query: str, limit: int = 10, page: int = 1) -> SearchResult:
    """
    Search for contacts in the CRM system with advanced filtering.
    
    This tool returns a complex SearchResult object containing:
    - Query metadata (query string, pagination info)
    - List of ContactInfo objects with full contact details
    - Pagination information
    
    Args:
        query: Search query string to find contacts
        limit: Maximum number of results per page (default: 10)
        page: Page number for pagination (default: 1)
    
    Returns:
        SearchResult object with matching contacts and metadata
    """
    # Simulate search results
    contacts = [
        ContactInfo(
            id=i,
            name=f"Contact {i}",
            email=f"contact{i}@example.com",
            phone=f"+1-555-000{i}",
            company=f"Company {i % 3}",
            tags=["customer", "active"] if i % 2 == 0 else ["lead"]
        )
        for i in range(1, min(limit + 1, 6))
    ]
    
    return SearchResult(
        query=query,
        total_results=50,
        page=page,
        results=contacts,
        has_more=True
    )


@tool
def create_contact_advanced(
    name: str,
    email: str,
    phone: str | None = None,
    company: str | None = None,
    tags: List[str] | None = None
) -> ContactInfo:
    """
    Create a new contact in the CRM system.
    
    This tool returns a ContactInfo Pydantic model with all contact details.
    The OpenAPI spec will automatically reflect the structure of ContactInfo.
    
    Args:
        name: Full name of the contact
        email: Email address of the contact
        phone: Optional phone number
        company: Optional company name
        tags: Optional list of tags for categorization
    
    Returns:
        ContactInfo object with the created contact details
    """
    return ContactInfo(
        id=12345,
        name=name,
        email=email,
        phone=phone,
        company=company,
        tags=tags or []
    )


@tool
def send_email_advanced(to: str, subject: str, body: str) -> EmailResult:
    """
    Send an email to a recipient with detailed result tracking.
    
    This tool returns an EmailResult object containing:
    - Success status
    - Recipient information
    - Email metadata (timestamp, message ID, delivery status)
    
    Args:
        to: Email address of the recipient
        subject: Subject line of the email
        body: Body content of the email
    
    Returns:
        EmailResult object with sending status and metadata
    """
    return EmailResult(
        success=True,
        recipient=to,
        subject=subject,
        metadata=EmailMetadata(
            sent_at=datetime.now().isoformat(),
            message_id=f"msg_{hash(to + subject)}",
            status="delivered"
        )
    )


@tool
def get_weather_detailed(location: str, units: str = "celsius") -> WeatherData:
    """
    Get detailed weather information for a location.
    
    This tool returns a WeatherData object with comprehensive weather information:
    - Current conditions (temperature, humidity, wind)
    - 3-day forecast
    - Location details
    
    The OpenAPI spec will show the full nested structure including the forecast array.
    
    Args:
        location: City name or location to get weather for
        units: Temperature units - 'celsius' or 'fahrenheit' (default: celsius)
    
    Returns:
        WeatherData object with current conditions and forecast
    """
    return WeatherData(
        location=location,
        temperature=22.5 if units == "celsius" else 72.5,
        units=units,
        conditions="Partly Cloudy",
        humidity=65,
        wind_speed=12.5,
        forecast=[
            {"day": "Tomorrow", "high": 24, "low": 18, "conditions": "Sunny"},
            {"day": "Day 2", "high": 23, "low": 17, "conditions": "Cloudy"},
            {"day": "Day 3", "high": 21, "low": 16, "conditions": "Rainy"}
        ]
    )


@tool
def get_contact_list(limit: int = 10) -> List[ContactInfo]:
    """
    Get a list of all contacts.
    
    This tool returns a List of ContactInfo objects.
    The OpenAPI spec will represent this as an array of ContactInfo schemas.
    
    Args:
        limit: Maximum number of contacts to return
    
    Returns:
        List of ContactInfo objects
    """
    return [
        ContactInfo(
            id=i,
            name=f"Contact {i}",
            email=f"contact{i}@example.com",
            phone=f"+1-555-000{i}",
            company=f"Company {i % 3}",
            tags=["customer"] if i % 2 == 0 else ["lead"]
        )
        for i in range(1, min(limit + 1, 11))
    ]


@tool
def get_contact_stats() -> Dict[str, Any]:
    """
    Get statistics about contacts in the system.
    
    This tool returns a nested dictionary structure.
    The OpenAPI spec will represent this as an object with nested properties.
    
    Returns:
        Dictionary with contact statistics including counts by category and recent activity
    """
    return {
        "total_contacts": 1250,
        "by_category": {
            "customers": 800,
            "leads": 350,
            "partners": 100
        },
        "recent_activity": {
            "added_today": 15,
            "updated_today": 42,
            "emails_sent_today": 127
        },
        "top_companies": [
            {"name": "Acme Corp", "contact_count": 45},
            {"name": "TechStart Inc", "contact_count": 38},
            {"name": "Global Solutions", "contact_count": 32}
        ]
    }


async def example_convert_tools_to_oas():
    """
    Example: Convert CUGA tools to OpenAPI specification.
    
    This example demonstrates how simple and complex return types are represented
    in the generated OpenAPI specification.
    """
    logger.info("Creating tool provider with sample tools...")
    
    # Create a direct tool provider with sample tools (mix of simple and complex)
    sample_tools = [
        calculate,  # Simple: returns str
        search_contacts_advanced,  # Complex: returns SearchResult (Pydantic model)
        create_contact_advanced,  # Complex: returns ContactInfo (Pydantic model)
        send_email_advanced,  # Complex: returns EmailResult with nested EmailMetadata
        get_weather_detailed,  # Complex: returns WeatherData with forecast array
        get_contact_list,  # Complex: returns List[ContactInfo]
        get_contact_stats,  # Complex: returns nested Dict
    ]
    
    provider = DirectLangChainToolsProvider(tools=sample_tools, app_name="demo_app")
    
    # Initialize the provider
    await provider.initialize()
    
    logger.info("Converting tools to OpenAPI specification...")
    logger.info("Note: Complex return types (Pydantic models, nested dicts, lists) will be")
    logger.info("      fully represented in the OpenAPI schema with their complete structure.")
    
    # Convert all tools to OpenAPI format
    oas = await cuga_tools_to_oas(
        tool_provider=provider,
        title="CUGA Tools API - Complex Objects Demo",
        version="1.0.0",
        description="OpenAPI specification demonstrating complex return types including Pydantic models, nested objects, and lists",
    )
    
    # Save to file
    output_path = Path("cuga_tools_openapi.yaml")
    oas.save(output_path)
    logger.info(f"OpenAPI specification saved to {output_path}")
    logger.info("Check the file to see how complex objects are represented in the schema!")
    
    # Also save as JSON
    json_path = Path("cuga_tools_openapi.json")
    oas.save(json_path)
    logger.info(f"OpenAPI specification saved to {json_path}")
    
    return oas


async def example_convert_to_toolinfo():
    """
    Example: Convert CUGA tools to Toolguard ToolInfo objects.
    
    This demonstrates how complex return types are preserved through the
    OpenAPI -> ToolInfo conversion process.
    """
    from toolguard.buildtime.gen_spec.oas_to_toolinfo import openapi_to_toolinfos
    
    logger.info("Creating tool provider with complex return type tools...")
    
    # Create a direct tool provider with tools that return complex objects
    sample_tools = [
        calculate,
        search_contacts_advanced,
        create_contact_advanced,
        send_email_advanced,
        get_weather_detailed,
        get_contact_list,
        get_contact_stats,
    ]
    
    provider = DirectLangChainToolsProvider(tools=sample_tools, app_name="demo_app")
    await provider.initialize()
    
    logger.info("Converting tools to OpenAPI specification...")
    
    # Convert to OpenAPI
    oas = await cuga_tools_to_oas(provider)
    
    logger.info("Converting OpenAPI to ToolInfo objects...")
    
    # Convert OpenAPI to ToolInfo objects (for Toolguard policy enforcement)
    tool_infos = openapi_to_toolinfos(oas)
    
    logger.info(f"Converted {len(tool_infos)} tools to ToolInfo objects")
    logger.info("Complex return types are preserved in the ToolInfo schema!")
    
    # Print some information about the tools
    for tool_info in tool_infos:
        logger.info(f"\nTool: {tool_info.name}")
        logger.info(f"  Summary: {tool_info.summary}")
        logger.info(f"  Parameters: {list(tool_info.parameters.keys())}")
        # Note: Response schema information is also preserved in the ToolInfo
    
    return tool_infos


async def example_with_multiple_apps():
    """
    Example: Convert tools from multiple app groups with complex return types.
    
    This shows how different apps can have tools with different complexity levels.
    """
    logger.info("Creating tool provider with multiple app groups...")
    
    # Create CRM tools (complex return types)
    crm_tools = [search_contacts_advanced, create_contact_advanced, get_contact_list]
    crm_provider = DirectLangChainToolsProvider(tools=crm_tools, app_name="crm")
    await crm_provider.initialize()
    
    # Create email tools (complex return types)
    email_tools = [send_email_advanced]
    email_provider = DirectLangChainToolsProvider(tools=email_tools, app_name="email")
    await email_provider.initialize()
    
    # Create weather tools (complex return types)
    weather_tools = [get_weather_detailed]
    weather_provider = DirectLangChainToolsProvider(tools=weather_tools, app_name="weather")
    await weather_provider.initialize()
    
    logger.info("Converting CRM tools to OpenAPI specification...")
    
    # Convert CRM tools to OpenAPI
    crm_oas = await cuga_tools_to_oas(
        tool_provider=crm_provider,
        title="CRM Tools API",
        version="1.0.0",
        description="OpenAPI specification for CRM tools with complex ContactInfo and SearchResult models",
    )
    
    logger.info("Converting Email tools to OpenAPI specification...")
    
    # Convert Email tools to OpenAPI
    email_oas = await cuga_tools_to_oas(
        tool_provider=email_provider,
        title="Email Tools API",
        version="1.0.0",
        description="OpenAPI specification for Email tools with EmailResult and nested EmailMetadata",
    )
    
    logger.info("Converting Weather tools to OpenAPI specification...")
    
    # Convert Weather tools to OpenAPI
    weather_oas = await cuga_tools_to_oas(
        tool_provider=weather_provider,
        title="Weather Tools API",
        version="1.0.0",
        description="OpenAPI specification for Weather tools with WeatherData including forecast arrays",
    )
    
    # Save to files
    crm_path = Path("crm_tools_openapi.yaml")
    crm_oas.save(crm_path)
    logger.info(f"CRM OpenAPI specification saved to {crm_path}")
    
    email_path = Path("email_tools_openapi.yaml")
    email_oas.save(email_path)
    logger.info(f"Email OpenAPI specification saved to {email_path}")
    
    weather_path = Path("weather_tools_openapi.yaml")
    weather_oas.save(weather_path)
    logger.info(f"Weather OpenAPI specification saved to {weather_path}")
    
    logger.info("\nAll OpenAPI specs include full schema definitions for complex return types!")
    
    return crm_oas, email_oas, weather_oas


async def example_complex_objects_showcase():
    """
    Example: Showcase how different complex return types are represented in OpenAPI.
    
    This example demonstrates:
    1. Pydantic models -> Full JSON schema with all fields
    2. List[PydanticModel] -> Array schema with item definitions
    3. Nested objects -> Hierarchical schema structure
    4. Dict[str, Any] -> Object schema with nested properties
    """
    logger.info("=" * 80)
    logger.info("COMPLEX OBJECT RETURN TYPES SHOWCASE")
    logger.info("=" * 80)
    
    # Create provider with one tool of each type
    showcase_tools = [
        create_contact_advanced,      # Returns: ContactInfo (Pydantic model)
        get_contact_list,             # Returns: List[ContactInfo]
        send_email_advanced,          # Returns: EmailResult with nested EmailMetadata
        get_weather_detailed,         # Returns: WeatherData with forecast array
        get_contact_stats,            # Returns: Dict[str, Any] with nested structure
    ]
    
    provider = DirectLangChainToolsProvider(tools=showcase_tools, app_name="showcase")
    await provider.initialize()
    
    logger.info("\nConverting tools with complex return types to OpenAPI...")
    
    oas = await cuga_tools_to_oas(
        tool_provider=provider,
        title="Complex Return Types Showcase",
        version="1.0.0",
        description="Demonstrates how various complex return types are represented in OpenAPI specs",
    )
    
    # Save the spec
    output_path = Path("complex_objects_showcase.yaml")
    oas.save(output_path)
    
    logger.info(f"\n✅ OpenAPI spec saved to: {output_path}")
    logger.info("\nWhat to look for in the generated spec:")
    logger.info("  1. 'components/schemas' section - Contains all Pydantic model definitions")
    logger.info("  2. Each tool's response schema - References the component schemas")
    logger.info("  3. Nested objects - Fully expanded with all properties")
    logger.info("  4. Arrays - Properly typed with 'items' schema")
    logger.info("  5. Optional fields - Marked appropriately in the schema")
    
    logger.info("\n" + "=" * 80)
    logger.info("EXAMPLE OUTPUT STRUCTURES:")
    logger.info("=" * 80)
    
    # Show example outputs
    logger.info("\n1. ContactInfo (Pydantic Model):")
    logger.info("   {")
    logger.info('     "id": 12345,')
    logger.info('     "name": "John Doe",')
    logger.info('     "email": "john@example.com",')
    logger.info('     "phone": "+1-555-0001",')
    logger.info('     "company": "Acme Corp",')
    logger.info('     "tags": ["customer", "vip"]')
    logger.info("   }")
    
    logger.info("\n2. List[ContactInfo]:")
    logger.info("   [")
    logger.info("     { ContactInfo object 1 },")
    logger.info("     { ContactInfo object 2 },")
    logger.info("     ...")
    logger.info("   ]")
    
    logger.info("\n3. EmailResult with nested EmailMetadata:")
    logger.info("   {")
    logger.info('     "success": true,')
    logger.info('     "recipient": "user@example.com",')
    logger.info('     "subject": "Hello",')
    logger.info('     "metadata": {')
    logger.info('       "sent_at": "2024-01-01T12:00:00",')
    logger.info('       "message_id": "msg_12345",')
    logger.info('       "status": "delivered"')
    logger.info("     }")
    logger.info("   }")
    
    logger.info("\n4. WeatherData with forecast array:")
    logger.info("   {")
    logger.info('     "location": "New York",')
    logger.info('     "temperature": 22.5,')
    logger.info('     "forecast": [')
    logger.info('       {"day": "Tomorrow", "high": 24, "low": 18},')
    logger.info("       ...")
    logger.info("     ]")
    logger.info("   }")
    
    logger.info("\n5. Nested Dict[str, Any]:")
    logger.info("   {")
    logger.info('     "total_contacts": 1250,')
    logger.info('     "by_category": {')
    logger.info('       "customers": 800,')
    logger.info('       "leads": 350')
    logger.info("     },")
    logger.info('     "top_companies": [')
    logger.info('       {"name": "Acme", "contact_count": 45}')
    logger.info("     ]")
    logger.info("   }")
    
    logger.info("\n" + "=" * 80)
    logger.info("✅ All these structures are fully represented in the OpenAPI spec!")
    logger.info("=" * 80)
    
    return oas


async def main():
    """
    Run all examples demonstrating complex object return types.
    """
    logger.info("\n" + "🚀 " * 20)
    logger.info("CUGA TO TOOLGUARD - COMPLEX OBJECT EXAMPLES")
    logger.info("🚀 " * 20 + "\n")
    
    logger.info("=" * 80)
    logger.info("Example 1: Complex Objects Showcase")
    logger.info("=" * 80)
    try:
        await example_complex_objects_showcase()
    except Exception as e:
        logger.error(f"Example 1 failed: {e}")
        import traceback
        traceback.print_exc()
    
    logger.info("\n" + "=" * 80)
    logger.info("Example 2: Convert all tools to OpenAPI")
    logger.info("=" * 80)
    try:
        await example_convert_tools_to_oas()
    except Exception as e:
        logger.error(f"Example 2 failed: {e}")
        import traceback
        traceback.print_exc()
    
    logger.info("\n" + "=" * 80)
    logger.info("Example 3: Convert to ToolInfo objects")
    logger.info("=" * 80)
    try:
        await example_convert_to_toolinfo()
    except Exception as e:
        logger.error(f"Example 3 failed: {e}")
        import traceback
        traceback.print_exc()
    
    logger.info("\n" + "=" * 80)
    logger.info("Example 4: Convert multiple app groups")
    logger.info("=" * 80)
    try:
        await example_with_multiple_apps()
    except Exception as e:
        logger.error(f"Example 4 failed: {e}")
        import traceback
        traceback.print_exc()
    
    logger.info("\n" + "🎉 " * 20)
    logger.info("ALL EXAMPLES COMPLETED!")
    logger.info("🎉 " * 20 + "\n")


if __name__ == "__main__":
    asyncio.run(main())


# ============================================================================
# SUMMARY: Complex Object Return Types in OpenAPI Specs
# ============================================================================
#
# This file demonstrates that YES, OpenAPI specs WILL reflect complex object
# return types! Here's what's included:
#
# 1. PYDANTIC MODELS (ContactInfo, EmailResult, WeatherData, etc.)
#    - Automatically converted to JSON Schema
#    - All fields with proper types (int, str, bool, etc.)
#    - Nested models preserved (EmailResult contains EmailMetadata)
#    - Optional fields marked appropriately
#
# 2. LIST OF COMPLEX OBJECTS (List[ContactInfo], List[ProductModel])
#    - Represented as arrays in OpenAPI
#    - Item schema fully defined with all properties
#
# 3. NESTED DICTIONARIES (get_contact_stats returns Dict[str, Any])
#    - Hierarchical structure preserved
#    - Nested objects and arrays maintained
#
# 4. MIXED COMPLEXITY
#    - WeatherData includes both simple fields AND a forecast array
#    - SearchResult includes metadata AND a list of ContactInfo objects
#
# The generated OpenAPI specification will include:
# - Full schema definitions in 'components/schemas'
# - Response schemas for each tool referencing these components
# - Complete type information for all fields
# - Proper handling of optional/required fields
#
# Run this file to see the generated OpenAPI specs:
#   python src/cuga/backend/cuga_graph/policy/tool_guard/example_usage.py
#
# Then check the generated YAML/JSON files to see how complex objects are
# represented in the OpenAPI specification!
#
# Made with Bob

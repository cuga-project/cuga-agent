"""Centralised skill definitions for e2e tests.

Import constants from here instead of duplicating inline skill strings across test modules.
"""

from __future__ import annotations

from dataclasses import dataclass

from cuga.backend.skills.registry import SkillEntry


# ---------------------------------------------------------------------------
# Simple SkillEntry objects — used in Tier-1 component tests
# ---------------------------------------------------------------------------

SINGLE_SKILL = SkillEntry("my_skill", "My skill", "## Body", "skills/my_skill/SKILL.md")
ALPHA_SKILL = SkillEntry("alpha", "Alpha skill", "## Body", "skills/alpha/SKILL.md")
BETA_SKILL = SkillEntry("beta", "Beta skill", "## Body", "skills/beta/SKILL.md")
GAMMA_SKILL = SkillEntry("gamma", "Gamma makes reports", "## Body", "skills/gamma/SKILL.md")


# ---------------------------------------------------------------------------
# Fabricated/proprietary skills — used in Tier-2 and Tier-3 tests
# ---------------------------------------------------------------------------


@dataclass
class SkillSpec:
    """Bundles everything a test needs to write and assert against a skill."""

    name: str
    description: str
    body: str
    task: str = ""
    expected: str | tuple[str, ...] = ""


# Coefficients (14, 3, 5, +22) are fabricated — no LLM can produce 159 without reading the body.
COMPLIANCE_SCORER = SkillSpec(
    name="acme_compliance_scorer",
    description="Computes the Acme Corp proprietary compliance risk score for audit findings",
    body=(
        "## Acme Corp Compliance Risk Score Calculator\n\n"
        "Use this proprietary formula to compute the CRS (Compliance Risk Score):\n\n"
        "    CRS = (violations * 14) + (days_overdue * 3) - (controls_passed * 5) + 22\n\n"
        "Where:\n"
        "- violations: number of distinct policy violations found\n"
        "- days_overdue: number of calendar days past the remediation deadline\n"
        "- controls_passed: number of controls that passed review in the same audit cycle\n"
        "- The constant offset 22 is the Acme baseline risk factor\n\n"
        'Report the result as: "Acme CRS: <number>"'
    ),
    task="Compute the Acme compliance risk score for: 3 violations, 45 days overdue, 8 controls passed.",
    expected="159",
)

# PRU-2267-K is a made-up identifier absent from all public training data.
PARTS_CATALOG = SkillSpec(
    name="parts_catalog_lookup",
    description="Returns internal part codes from the Acme Corp industrial parts catalog",
    body=(
        "## Acme Corp Parts Catalog - Internal Reference\n\n"
        "Return the exact internal part code for the requested product.\n\n"
        "| Product Description             | Internal Part Code |\n"
        "|---------------------------------|--------------------|\n"
        "| Thermal Bypass Valve            | TBV-9143-X         |\n"
        "| Pressure Relief Unit            | PRU-2267-K         |\n"
        "| Flow Control Module             | FCM-5508-J         |\n"
        "| Rotary Actuator Assembly Type-3 | RAA-7712-Q         |\n"
        "| Solenoid Isolation Block        | SIB-3391-N         |\n\n"
        'If the product is not listed, respond: "Part code not found in catalog."'
    ),
    task="What is the Acme Corp internal part code for the Pressure Relief Unit?",
    expected="PRU-2267-K",
)

# NEXUS / CERBERUS / IRONGATE / DOCUVAULT are invented system names.
VENDOR_ONBOARDING = SkillSpec(
    name="acme_vendor_onboarding",
    description="Guides the Acme Corp vendor onboarding process with all required internal steps",
    body=(
        "## Acme Corp Vendor Onboarding - Standard Process v4.2\n\n"
        "Complete all steps in order. Do not skip or reorder.\n\n"
        "Step 1 - NEXUS Compliance Screen\n"
        "  Submit vendor details to the NEXUS compliance portal (portal ID: NX-VENDOR).\n"
        "  Await NEXUS clearance code before proceeding.\n\n"
        "Step 2 - CERBERUS Authentication Setup\n"
        "  Create vendor account in CERBERUS (internal IAM system).\n"
        "  Assign role: VENDOR_EXTERNAL_L1.\n\n"
        "Step 3 - IRONGATE Financial Vetting\n"
        "  Submit bank details and tax forms to IRONGATE (finance validation system).\n"
        "  Record the IRONGATE approval reference number.\n\n"
        "Step 4 - Master Agreement via DOCUVAULT\n"
        "  Send the standard MSA template via DOCUVAULT (contract management portal).\n"
        "  DOCUVAULT signatures only - do not use email attachments.\n\n"
        "Step 5 - Activation Confirmation\n"
        "  Confirm all prior steps, then issue activation. Reference the NEXUS clearance\n"
        "  code, CERBERUS activation token, and IRONGATE reference number.\n\n"
        "Always name all four internal systems in your summary: "
        "NEXUS, CERBERUS, IRONGATE, DOCUVAULT."
    ),
    task="Walk me through the Acme Corp vendor onboarding process.",
    expected=("NEXUS", "CERBERUS", "IRONGATE", "DOCUVAULT"),
)

# Simple skills for Tier-2 graph integration tests (no fabricated data needed).
SUMMARIZE_REPORT = SkillSpec(
    name="summarize_report",
    description="Summarizes complex reports into bullet points",
    body="## Instructions\nRead the report and summarize.",
)

DATA_EXTRACTOR = SkillSpec(
    name="data_extractor",
    description="Extracts structured data",
    body="## Extract data",
)

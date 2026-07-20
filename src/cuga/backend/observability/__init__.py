"""
Cuga observability integrations.

Currently supported:
- OpenLit: LLM observability via OpenTelemetry (OTLP)
  Enable via settings.toml: [observability] openlit = true
  Install: pip install cuga[observability]
  Airgapped: uses bundled pricing.json (override with DYNACONF_OBSERVABILITY__PRICING_JSON)
"""

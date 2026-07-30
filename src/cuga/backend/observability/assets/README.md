# OpenLit pricing asset

`pricing.json` is vendored from the [OpenLit](https://github.com/openlit/openlit)
project (`assets/pricing.json`) so airgapped deployments can initialize OpenLit
without fetching `raw.githubusercontent.com`.

Refresh periodically against upstream when updating the `openlit` dependency.

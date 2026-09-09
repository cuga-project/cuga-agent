#!/usr/bin/env python3
"""Render a cloned CUGA agent Deployment/Service/Route from a live one.

Reads the source agent's Deployment JSON on stdin and emits a List of objects
on stdout. Derived from the running Deployment rather than hand-authored so the
clone inherits Vault, Postgres, model and CA config automatically — the only
things overridden are identity, image, auth, TLS and the Forge wiring.

See QUICKSTART.md step 4.
"""

import argparse
import json
import sys

# Env the clone must NOT inherit verbatim from its source.
#   identity  -> config rows are scoped by (tenant, instance, agent); reusing the
#                source's instance id would have both agents writing the same rows
#   OIDC/TLS  -> the clone gets a new hostname the Verify app has no redirect URI
#                for, and the source's TLS cert is issued for the source's host.
#                Serving plain HTTP behind an edge-terminated route sidesteps both;
#                the PoC is testing Forge auth, not CUGA login (plan Track E).
_DROP = {
    "DYNACONF_SERVICE__INSTANCE_ID",
    "OIDC_CLIENT_ID",
    "OIDC_CLIENT_SECRET",
    "OIDC_DISCOVERY_URL",
    "OIDC_REDIRECT_URI",
    "SSL_KEYFILE",
    "SSL_CERTFILE",
    "DYNACONF_AUTH__ENABLED",
    "DYNACONF_AUTH__AUTHORIZATION_ENABLED",
    "DYNACONF_AUTH__REQUIRE_HTTPS",
}


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--name", required=True)
    p.add_argument("--instance-id", required=True)
    p.add_argument("--agent-id", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--namespace", required=True)
    p.add_argument("--cluster-domain", required=True)
    p.add_argument("--forge-url", required=True)
    p.add_argument("--forge-audience", required=True)
    p.add_argument("--forge-workspace-group", required=True)
    p.add_argument("--forge-token", required=True)
    p.add_argument("--dbs-size", default="1Gi")
    args = p.parse_args()

    src = json.load(sys.stdin)
    pod = src["spec"]["template"]["spec"]
    container = pod["containers"][0]

    env = [e for e in container.get("env", []) if e.get("name") not in _DROP]
    env += [
        {"name": "DYNACONF_SERVICE__INSTANCE_ID", "value": args.instance_id},
        {"name": "AGENT_ID", "value": args.agent_id},
        # Auth off: see _DROP above.
        {"name": "DYNACONF_AUTH__ENABLED", "value": "false"},
        {"name": "DYNACONF_AUTH__AUTHORIZATION_ENABLED", "value": "false"},
        {"name": "DYNACONF_AUTH__REQUIRE_HTTPS", "value": "false"},
        {"name": "DYNACONF_CONTEXT_FORGE__ENABLED", "value": "true"},
        {"name": "DYNACONF_CONTEXT_FORGE__URL", "value": args.forge_url},
        {"name": "DYNACONF_CONTEXT_FORGE__AUDIENCE", "value": args.forge_audience},
        {"name": "DYNACONF_CONTEXT_FORGE__WORKSPACE_GROUP", "value": args.forge_workspace_group},
        {"name": "DYNACONF_CONTEXT_FORGE__TOKEN_SOURCE", "value": "env"},
        # Forge's route is signed by the cluster's internal CA. The pod already
        # gets REQUESTS_CA_BUNDLE/SSL_CERT_FILE from its source, but those point
        # at the Vault CA, not the ingress CA — so verification is off for the
        # PoC rather than shipping a second bundle.
        {"name": "DYNACONF_CONTEXT_FORGE__VERIFY_SSL", "value": "false"},
        {"name": "CONTEXT_FORGE_TOKEN", "value": args.forge_token},
    ]
    container["env"] = env
    container["image"] = args.image
    container["name"] = "cuga"
    container["ports"] = [{"containerPort": 7860, "name": "http", "protocol": "TCP"}]

    # Source probes target the HTTPS port; the clone serves plain HTTP.
    for probe in ("readinessProbe", "livenessProbe", "startupProbe"):
        pr = container.get(probe)
        if isinstance(pr, dict) and "httpGet" in pr:
            pr["httpGet"]["scheme"] = "HTTP"
            pr["httpGet"]["port"] = 7860

    # Keep only the dbs volume — the other two are the source agent's per-instance
    # TLS cert and config secret, neither of which applies to the clone.
    #
    # dbs is a ReadWriteOnce PVC, one per agent, mounted at CUGA_DBS_DIR. It
    # CANNOT be shared: pointing the clone at the source's claim schedules the
    # pod and then hangs forever in ContainerCreating with
    # "Multi-Attach error for volume ... already used by pod(s) <source>".
    # Give the clone its own claim, matching how real agents are provisioned.
    keep_mounts = [m for m in container.get("volumeMounts", []) if m["name"] == "dbs"]
    container["volumeMounts"] = keep_mounts
    pod["volumes"] = [{"name": "dbs", "persistentVolumeClaim": {"claimName": args.name}}]

    labels = {"app": args.name, "cuga.ibm.com/poc": "mcpcf"}
    pvc = {
        "apiVersion": "v1",
        "kind": "PersistentVolumeClaim",
        "metadata": {"name": args.name, "namespace": args.namespace, "labels": labels},
        "spec": {
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": args.dbs_size}},
        },
    }
    deployment = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        # No ownerReferences: the CugaAgent operator must neither manage nor
        # revert this Deployment. That is the whole reason for cloning.
        "metadata": {"name": args.name, "namespace": args.namespace, "labels": labels},
        "spec": {
            "replicas": 1,
            "strategy": {"type": "Recreate"},
            "selector": {"matchLabels": labels},
            "template": {"metadata": {"labels": labels}, "spec": pod},
        },
    }
    service = {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": args.name, "namespace": args.namespace, "labels": labels},
        "spec": {
            "selector": labels,
            # Named port — an unnamed one renders a router backend with zero
            # server lines and 503s forever with no diagnostic (see README.md).
            "ports": [{"name": "http", "port": 80, "targetPort": "http"}],
        },
    }
    route = {
        "apiVersion": "route.openshift.io/v1",
        "kind": "Route",
        "metadata": {"name": args.name, "namespace": args.namespace, "labels": labels},
        "spec": {
            "host": f"{args.name}.apps.{args.cluster_domain}",
            "to": {"kind": "Service", "name": args.name},
            "port": {"targetPort": "http"},
            "tls": {"termination": "edge", "insecureEdgeTerminationPolicy": "Redirect"},
        },
    }
    json.dump({"apiVersion": "v1", "kind": "List", "items": [pvc, deployment, service, route]}, sys.stdout)


if __name__ == "__main__":
    main()

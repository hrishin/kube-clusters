#!/usr/bin/env python3
"""
Waits for a Kubernetes LoadBalancer Service to get an external IP, then
upserts a Cloudflare A record pointing at it.

Invoked by dns.py as a `pulumi_command.local.Command` create/update step
(instead of a Pulumi dynamic provider) — dynamic providers are dill-pickled
and re-imported in a separate subprocess that only inherits PYTHONPATH from
the shell that launched `pulumi`, not the sys.path.insert() this cluster-infra
module relies on to be importable at all.

All configuration comes from environment variables so secrets never appear
in argv (visible in process listings):
  KUBECONFIG_CONTENT, CF_API_TOKEN          - secrets
  GATEWAY_NAMESPACE, GATEWAY_SERVICE_NAME
  CF_ZONE_NAME, RECORD_NAME, TTL, PROXIED
  POLL_TIMEOUT_SECONDS, POLL_INTERVAL_SECONDS

Prints {"ip": ..., "fqdn": ..., "record_id": ...} as JSON on success.
"""

import json
import os
import time

import requests
import yaml
from kubernetes import client as k8s_client
from kubernetes import config as k8s_config

CF_API = "https://api.cloudflare.com/client/v4"


def _wait_for_lb_ip(
    *,
    kubeconfig: str,
    namespace: str,
    service_name: str,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> str:
    api_client = k8s_config.new_client_from_config_dict(yaml.safe_load(kubeconfig))
    v1 = k8s_client.CoreV1Api(api_client)

    deadline = time.time() + timeout_seconds
    while True:
        try:
            svc = v1.read_namespaced_service(service_name, namespace)
            lb = svc.status.load_balancer if svc.status else None
            ingress = lb.ingress if lb else None
            if ingress and ingress[0].ip:
                return ingress[0].ip
        except k8s_client.exceptions.ApiException as exc:
            if exc.status != 404:
                raise
        if time.time() >= deadline:
            raise TimeoutError(
                f"Timed out after {timeout_seconds}s waiting for LoadBalancer IP "
                f"on {namespace}/{service_name}. Has Flux reconciled the Gateway yet?"
            )
        time.sleep(poll_interval_seconds)


def _cf_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _upsert_cf_record(
    *,
    token: str,
    zone_name: str,
    record_name: str,
    ip: str,
    ttl: int,
    proxied: bool,
) -> dict:
    headers = _cf_headers(token)

    zone_resp = requests.get(f"{CF_API}/zones", headers=headers, params={"name": zone_name})
    zone_resp.raise_for_status()
    zone_data = zone_resp.json()
    if not zone_data.get("success") or not zone_data["result"]:
        raise RuntimeError(f"Cloudflare zone lookup failed for {zone_name}: {zone_data}")
    zone_id = zone_data["result"][0]["id"]

    fqdn = f"{record_name}.{zone_name}"
    list_resp = requests.get(
        f"{CF_API}/zones/{zone_id}/dns_records",
        headers=headers,
        params={"type": "A", "name": fqdn},
    )
    list_resp.raise_for_status()
    list_data = list_resp.json()
    if not list_data.get("success"):
        raise RuntimeError(f"Cloudflare DNS record lookup failed for {fqdn}: {list_data}")
    existing = list_data["result"]

    body = {"type": "A", "name": record_name, "content": ip, "ttl": ttl, "proxied": proxied}
    if existing:
        record_id = existing[0]["id"]
        resp = requests.put(f"{CF_API}/zones/{zone_id}/dns_records/{record_id}", headers=headers, json=body)
    else:
        resp = requests.post(f"{CF_API}/zones/{zone_id}/dns_records", headers=headers, json=body)
    resp.raise_for_status()
    data = resp.json()
    if not data.get("success"):
        raise RuntimeError(f"Cloudflare DNS record upsert failed for {fqdn}: {data}")

    return {"record_id": data["result"]["id"], "fqdn": fqdn}


def main() -> None:
    ip = _wait_for_lb_ip(
        kubeconfig=os.environ["KUBECONFIG_CONTENT"],
        namespace=os.environ["GATEWAY_NAMESPACE"],
        service_name=os.environ["GATEWAY_SERVICE_NAME"],
        timeout_seconds=int(os.environ["POLL_TIMEOUT_SECONDS"]),
        poll_interval_seconds=int(os.environ["POLL_INTERVAL_SECONDS"]),
    )
    cf_result = _upsert_cf_record(
        token=os.environ["CF_API_TOKEN"],
        zone_name=os.environ["CF_ZONE_NAME"],
        record_name=os.environ["RECORD_NAME"],
        ip=ip,
        ttl=int(os.environ["TTL"]),
        proxied=os.environ.get("PROXIED", "false").lower() == "true",
    )
    print(json.dumps({"ip": ip, **cf_result}))


if __name__ == "__main__":
    main()

"""
Nebius MK8s node groups — mirrors the structure of the AWS node_groups module.
"""

from typing import Any, Dict, List

import pulumi
import pulumi_nebius as nebius


# Nebius taint effects use SCREAMING_SNAKE_CASE
_TAINT_EFFECT_MAP = {
    "NoSchedule":       "NO_SCHEDULE",
    "NoExecute":        "NO_EXECUTE",
    "PreferNoSchedule": "PREFER_NO_SCHEDULE",
}


def create_node_groups(
    cluster_name: str,
    cluster_id: pulumi.Output,
    subnet_id: pulumi.Output,
    node_groups: Dict[str, Any],
    provider: nebius.Provider,
) -> Dict[str, Any]:
    """
    Create Nebius MK8s node groups from config.

    Config keys per node group (mirrors the AWS YAML schema):
        platform       str   Nebius compute platform (e.g. cpu-e2, gpu-l40s-a)
        preset         str   Resource preset (e.g. 2vcpu-80gb, 8vcpu-320gb)
        desired_size   int   Initial / fixed node count
        min_size       int   Autoscaling minimum  (ignored if min==max)
        max_size       int   Autoscaling maximum  (ignored if min==max)
        preemptible    bool  Preemptible (spot-equivalent) nodes
        labels         dict  Kubernetes node labels
        taints         list  [{key, value, effect}] — effect in K8s style (NoSchedule etc.)
    """
    node_group_resources: Dict[str, nebius.Mk8sV1NodeGroup] = {}
    prev_ng: nebius.Mk8sV1NodeGroup | None = None  # enforce sequential creation

    for ng_name, ng_cfg in node_groups.items():
        pulumi.log.info(f"Creating Nebius node group: {ng_name}")

        labels: Dict[str, str] = ng_cfg.get("labels", {})
        raw_taints: List[Dict[str, str]] = ng_cfg.get("taints", [])
        taints = [
            nebius.Mk8sV1NodeGroupTemplateTaintArgs(
                key=t["key"],
                value=t.get("value", ""),
                effect=_TAINT_EFFECT_MAP.get(t["effect"], t["effect"]),
            )
            for t in raw_taints
        ]

        desired = ng_cfg.get("desired_size", 1)
        min_size = ng_cfg.get("min_size", desired)
        max_size = ng_cfg.get("max_size", desired)

        # fixed_node_count and autoscaling are mutually exclusive in Nebius
        fixed_node_count = None
        autoscaling = None
        if min_size == max_size:
            fixed_node_count = float(desired)
        else:
            autoscaling = nebius.Mk8sV1NodeGroupAutoscalingArgs(
                min_node_count=float(min_size),
                max_node_count=float(max_size),
            )

        preemptible = (
            nebius.Mk8sV1NodeGroupTemplatePreemptibleArgs()
            if ng_cfg.get("preemptible")
            else None
        )

        gpu_settings = None
        if ng_cfg.get("drivers_preset"):
            gpu_settings = nebius.Mk8sV1NodeGroupTemplateGpuSettingsArgs(
                drivers_preset=ng_cfg["drivers_preset"],
            )

        ng = nebius.Mk8sV1NodeGroup(
            f"{cluster_name}-{ng_name}",
            parent_id=cluster_id,
            name=f"{cluster_name}-{ng_name}",
            fixed_node_count=fixed_node_count,
            autoscaling=autoscaling,
            template=nebius.Mk8sV1NodeGroupTemplateArgs(
                resources=nebius.Mk8sV1NodeGroupTemplateResourcesArgs(
                    platform=ng_cfg["platform"],
                    preset=ng_cfg["preset"],
                ),
                gpu_settings=gpu_settings,
                preemptible=preemptible,
                network_interfaces=[
                    nebius.Mk8sV1NodeGroupTemplateNetworkInterfaceArgs(
                        subnet_id=subnet_id,
                        public_ip_address=nebius.Mk8sV1NodeGroupTemplateNetworkInterfacePublicIpAddressArgs(),
                    )
                ],
                metadata=nebius.Mk8sV1NodeGroupTemplateMetadataArgs(
                    labels=labels if labels else None,
                ),
                taints=taints if taints else None,
            ),
            opts=pulumi.ResourceOptions(
                provider=provider,
                depends_on=[prev_ng] if prev_ng else [],
            ),
        )

        node_group_resources[ng_name] = ng
        prev_ng = ng

    return {"node_groups": node_group_resources}

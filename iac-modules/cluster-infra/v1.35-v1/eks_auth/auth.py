"""
EKS Authentication configuration (aws-auth ConfigMap)
Equivalent to terraform/modules/eks-auth
"""

import hashlib
import json

import pulumi
import pulumi_kubernetes as k8s
import pulumiverse_time as time
from typing import List, Any, Optional
import yaml as yaml_lib


def create_eks_auth(
    cluster_name: str,
    cluster_endpoint: pulumi.Output,
    cluster_ca_certificate: pulumi.Output,
    node_role_arn: pulumi.Output,
    cluster_admin_user_arns: List[str],
    cluster: Any,
    region: str,
    additional_dependencies: Optional[List[pulumi.Resource]] = None,
) -> dict:
    """
    Create aws-auth ConfigMap for EKS cluster authentication
    
    Args:
        cluster_name: Name of the EKS cluster
        cluster_endpoint: Cluster endpoint URL
        cluster_ca_certificate: Base64-encoded cluster CA certificate
        node_role_arn: IAM role ARN for worker nodes
        cluster_admin_user_arns: List of IAM user ARNs for cluster admins
        cluster: EKS cluster resource (for dependency)
        region: AWS region where the cluster lives
        additional_dependencies: Optional list of resources the ConfigMap should depend on
        
    Returns:
        Dictionary containing auth resources
    """
    
    # Create Kubernetes provider
    k8s_provider = k8s.Provider(
        f"{cluster_name}-k8s-provider",
        kubeconfig=pulumi.Output.all(
            cluster_endpoint,
            cluster_ca_certificate,
        ).apply(lambda args: f"""apiVersion: v1
clusters:
- cluster:
    certificate-authority-data: {args[1]}
    server: {args[0]}
  name: {cluster_name}
contexts:
- context:
    cluster: {cluster_name}
    user: {cluster_name}
  name: {cluster_name}
current-context: {cluster_name}
kind: Config
preferences: {{}}
users:
- name: {cluster_name}
  user:
    exec:
      apiVersion: client.authentication.k8s.io/v1beta1
      command: aws
      args:
        - eks
        - get-token
        - --cluster-name
        - {cluster_name}
        - --region
        - {region}
"""),
        opts=pulumi.ResourceOptions(depends_on=[cluster]),
    )
    
    # Create aws-auth ConfigMap data
    def create_aws_auth_data(node_role):
        map_roles = [
            {
                "rolearn": node_role,
                "username": "system:node:{{EC2PrivateDNSName}}",
                "groups": ["system:bootstrappers", "system:nodes"]
            }
        ]
        
        map_users = [
            {
                "userarn": arn,
                "username": arn.split("/")[-1],
                "groups": ["system:masters"]
            }
            for arn in cluster_admin_user_arns
        ]
        
        return {
            "mapRoles": yaml_lib.dump(map_roles, default_flow_style=False),
            "mapUsers": yaml_lib.dump(map_users, default_flow_style=False),
        }
    
    aws_auth_data = node_role_arn.apply(create_aws_auth_data)
    
    # Create aws-auth ConfigMap
    configmap_depends_on = [cluster]
    if additional_dependencies:
        configmap_depends_on.extend(additional_dependencies)

    aws_auth_state_hash = aws_auth_data.apply(
        lambda data: hashlib.sha256(json.dumps(data, sort_keys=True).encode("utf-8")).hexdigest()
    )

    sleep_before_auth = time.Sleep(
        f"{cluster_name}-aws-auth-sleep",
        create_duration="30s",
        triggers=aws_auth_state_hash.apply(lambda digest: {"state_hash": digest}),
        opts=pulumi.ResourceOptions(depends_on=list(configmap_depends_on)),
    )
    configmap_depends_on.append(sleep_before_auth)

    
    aws_auth_configmap = k8s.core.v1.ConfigMap(
        "aws-auth",
        metadata=k8s.meta.v1.ObjectMetaArgs(
            name="aws-auth",
            namespace="kube-system",
        ),
        data=aws_auth_data,
        opts=pulumi.ResourceOptions(
            provider=k8s_provider,
            depends_on=configmap_depends_on,
        ),
    )
    
    return {
        # "sleep_before_auth": sleep_before_auth,
        "aws_auth_configmap": aws_auth_configmap,
        "k8s_provider": k8s_provider,
    }


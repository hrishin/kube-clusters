"""
EKS Cluster infrastructure
Equivalent to terraform/modules/eks-cluster
"""

import pulumi
import pulumi_aws as aws
import json
from typing import Dict, Any, List


def create_eks_cluster(
    cluster_name: str,
    cluster_version: str,
    public_subnet_ids: List[pulumi.Output],
    private_subnet_ids: List[pulumi.Output],
    cluster_admin_user_arns: List[str],
    tags: Dict[str, str],
) -> Dict[str, Any]:
    """
    Create EKS cluster and related IAM resources
    
    Args:
        cluster_name: Name of the EKS cluster
        cluster_version: Kubernetes version
        public_subnet_ids: List of public subnet IDs
        private_subnet_ids: List of private subnet IDs
        cluster_admin_user_arns: List of IAM user ARNs for cluster admin
        tags: Common tags to apply to resources
        
    Returns:
        Dictionary containing cluster resource outputs
    """
    
    # Create EKS Cluster Service Role
    cluster_role = aws.iam.Role(
        f"{cluster_name}-cluster-service-role",
        name=f"{cluster_name}-cluster-service-role",
        assume_role_policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "eks.amazonaws.com"
                    },
                    "Action": "sts:AssumeRole"
                }
            ]
        }),
        tags=tags,
    )
    
    # Attach AmazonEKSClusterPolicy to cluster role
    cluster_policy_attachment = aws.iam.RolePolicyAttachment(
        f"{cluster_name}-cluster-policy",
        policy_arn="arn:aws:iam::aws:policy/AmazonEKSClusterPolicy",
        role=cluster_role.name,
    )
    
    # Combine all subnet IDs
    all_subnet_ids = pulumi.Output.all(public_subnet_ids, private_subnet_ids).apply(
        lambda args: args[0] + args[1]
    )
    
    # Create EKS Cluster
    cluster = aws.eks.Cluster(
        cluster_name,
        name=cluster_name,
        role_arn=cluster_role.arn,
        version=cluster_version,
        vpc_config=aws.eks.ClusterVpcConfigArgs(
            subnet_ids=all_subnet_ids,
            endpoint_private_access=True,
            endpoint_public_access=True,
            public_access_cidrs=["0.0.0.0/0"],
        ),
        access_config=aws.eks.ClusterAccessConfigArgs(
            authentication_mode="API",
        ),
        enabled_cluster_log_types=["api"],
        bootstrap_self_managed_addons=False,
        tags={
            **tags,
            "Name": cluster_name,
        },
        opts=pulumi.ResourceOptions(
            depends_on=[cluster_policy_attachment],
        ),
    )
    
    # Create Node Group Service Role
    node_role = aws.iam.Role(
        f"{cluster_name}-node-group-service-role",
        name=f"{cluster_name}-node-group-service-role",
        assume_role_policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Service": "ec2.amazonaws.com"
                    },
                    "Action": "sts:AssumeRole"
                }
            ]
        }),
        tags=tags,
    )
    
    # Attach required policies to node role
    aws.iam.RolePolicyAttachment(
        f"{cluster_name}-node-AmazonEKSWorkerNodePolicy",
        policy_arn="arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy",
        role=node_role.name,
    )
    
    aws.iam.RolePolicyAttachment(
        f"{cluster_name}-node-AmazonEKS_CNI_Policy",
        policy_arn="arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy",
        role=node_role.name,
    )
    
    aws.iam.RolePolicyAttachment(
        f"{cluster_name}-node-AmazonEC2ContainerRegistryReadOnly",
        policy_arn="arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
        role=node_role.name,
    )
    
    aws.iam.RolePolicyAttachment(
        f"{cluster_name}-node-AmazonSSMManagedInstanceCore",
        policy_arn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore",
        role=node_role.name,
    )
    
    # Create custom node authentication policy
    node_auth_policy = aws.iam.Policy(
        f"{cluster_name}-node-auth-policy",
        name_prefix=f"{cluster_name}-node-auth-policy-",
        description="Policy for EKS node authentication",
        policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": [
                        "eks:DescribeCluster",
                        "eks:ListClusters",
                        "eks:DescribeNodegroup",
                        "eks:ListNodegroups",
                        "sts:AssumeRole",
                        "sts:TagSession",
                        "sts:GetCallerIdentity"
                    ],
                    "Resource": "*"
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "ec2:DescribeInstances",
                        "ec2:DescribeInstanceStatus",
                        "ec2:DescribeInstanceAttribute",
                        "ec2:DescribeImages",
                        "ec2:DescribeVolumes",
                        "ec2:DescribeSnapshots",
                        "ec2:DescribeSecurityGroups",
                        "ec2:DescribeSubnets",
                        "ec2:DescribeVpcs",
                        "ec2:DescribeNetworkInterfaces",
                        "ec2:DescribeTags",
                        "ec2:CreateTags",
                        "ec2:ModifyInstanceAttribute"
                    ],
                    "Resource": "*"
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "autoscaling:DescribeAutoScalingGroups",
                        "autoscaling:DescribeAutoScalingInstances",
                        "autoscaling:DescribeLaunchConfigurations",
                        "autoscaling:DescribeTags",
                        "autoscaling:SetDesiredCapacity",
                        "autoscaling:TerminateInstanceInAutoScalingGroup",
                        "autoscaling:UpdateAutoScalingGroup"
                    ],
                    "Resource": "*"
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "elasticloadbalancing:DescribeLoadBalancers",
                        "elasticloadbalancing:DescribeLoadBalancerAttributes",
                        "elasticloadbalancing:DescribeListeners",
                        "elasticloadbalancing:DescribeListenerCertificates",
                        "elasticloadbalancing:DescribeSSLPolicies",
                        "elasticloadbalancing:DescribeRules",
                        "elasticloadbalancing:DescribeTargetGroups",
                        "elasticloadbalancing:DescribeTargetGroupAttributes",
                        "elasticloadbalancing:DescribeTargetHealth"
                    ],
                    "Resource": "*"
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "iam:ListRoles",
                        "iam:PassRole"
                    ],
                    "Resource": "*"
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogGroups",
                        "logs:DescribeLogStreams"
                    ],
                    "Resource": "*"
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "kms:Decrypt",
                        "kms:DescribeKey",
                        "kms:Encrypt",
                        "kms:GenerateDataKey",
                        "kms:GenerateDataKeyWithoutPlaintext",
                        "kms:ReEncryptFrom",
                        "kms:ReEncryptTo"
                    ],
                    "Resource": "*"
                },
                {
                    "Effect": "Allow",
                    "Action": [
                        "secretsmanager:GetSecretValue",
                        "secretsmanager:DescribeSecret"
                    ],
                    "Resource": "*"
                }
            ]
        }),
        tags=tags,
    )
    
    aws.iam.RolePolicyAttachment(
        f"{cluster_name}-node-auth-policy-attachment",
        policy_arn=node_auth_policy.arn,
        role=node_role.name,
    )
    
    # Create IAM Instance Profile for Nodes
    node_instance_profile = aws.iam.InstanceProfile(
        f"{cluster_name}-node-instance-profile",
        name=f"{cluster_name}-node-instance-profile",
        role=node_role.name,
        tags=tags,
    )
    
    # Get OIDC issuer URL and create thumbprint
    oidc_issuer = cluster.identities[0].oidcs[0].issuer
    
    # Create OIDC Identity Provider
    oidc_provider = aws.iam.OpenIdConnectProvider(
        f"{cluster_name}-oidc-provider",
        client_id_lists=["sts.amazonaws.com"],
        thumbprint_lists=[
            # EKS OIDC thumbprint (this is a known value for all EKS clusters)
            "9e99a48a9960b14926bb7f3b02e22da2b0ab7280"
        ],
        url=oidc_issuer,
        tags=tags,
    )
    
    # Create EKS Access Entry for cluster admins
    admin_access_policy_associations = []
    for i, admin_arn in enumerate(cluster_admin_user_arns):
        access_entry = aws.eks.AccessEntry(
            f"{cluster_name}-admin-{i}",
            cluster_name=cluster.name,
            principal_arn=admin_arn,
            type="STANDARD",
            opts=pulumi.ResourceOptions(depends_on=[cluster]),
        )
        
        # Associate admin policy
        admin_policy_association = aws.eks.AccessPolicyAssociation(
            f"{cluster_name}-admin-policy-{i}",
            cluster_name=cluster.name,
            principal_arn=admin_arn,
            policy_arn="arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy",
            access_scope=aws.eks.AccessPolicyAssociationAccessScopeArgs(
                type="cluster",
            ),
            opts=pulumi.ResourceOptions(depends_on=[access_entry]),
        )
        admin_access_policy_associations.append(admin_policy_association)
    
    # Create EKS Access Entry for node role
    #TODO: review
    node_access_entry = aws.eks.AccessEntry(
        f"{cluster_name}-node-role-access",
        cluster_name=cluster.name,
        principal_arn=node_role.arn,
        type="EC2_LINUX",
        opts=pulumi.ResourceOptions(depends_on=[cluster]),
    )

    pod_identity_agent = aws.eks.Addon(
        f"{cluster_name}-pod-identity-agent",
        cluster_name=cluster.name,
        addon_name="eks-pod-identity-agent",
        resolve_conflicts_on_create="OVERWRITE",
        resolve_conflicts_on_update="OVERWRITE",
        tags={**tags, "Name": f"{cluster_name}-pod-identity-agent"},
        opts=pulumi.ResourceOptions(depends_on=[cluster]),
    )

    return {
        "cluster": cluster,
        "cluster_endpoint": cluster.endpoint,
        "cluster_security_group_id": cluster.vpc_config.cluster_security_group_id,
        "cluster_certificate_authority_data": cluster.certificate_authority.data,
        "cluster_oidc_issuer_url": oidc_issuer,
        "cluster_oidc_provider_arn": oidc_provider.arn,
        "node_group_service_role_arn": node_role.arn,
        "node_role_name": node_role.name,
        "node_instance_profile_name": node_instance_profile.name,
        "cluster_service_role_arn": cluster_role.arn,
        "cluster_admin_access_policy_associations": admin_access_policy_associations,
        "node_access_entry": node_access_entry,
        "pod_identity_agent_addon": pod_identity_agent,
    }


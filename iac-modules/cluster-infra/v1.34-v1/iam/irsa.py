import json
import os
from typing import Any, Dict

import pulumi
import pulumi_aws as aws


def create_aws_lbc_irsa(
    cluster_name: str,
    oidc_issuer_url: pulumi.Output,
    oidc_provider_arn: pulumi.Output,
    tags: Dict[str, str],
) -> Dict[str, Any]:
    """
    Create IRSA (IAM Role for Service Accounts) for AWS Load Balancer Controller
    """

    # Parse the downloaded IAM policy and create an AWS IAM Policy
    policy_file_path = os.path.join(os.path.dirname(__file__), "aws_lbc_iam_policy.json")
    with open(policy_file_path, "r") as f:
        policy_document = f.read()

    aws_lbc_policy = aws.iam.Policy(
        f"{cluster_name}-aws-lbc-policy",
        name=f"{cluster_name}-aws-load-balancer-controller",
        policy=policy_document,
        tags=tags,
    )

    # The service account namespace and name as deployed by the Helm chart
    sa_namespace = "aws-load-balancer-controller"
    sa_name = "aws-load-balancer-controller"

    # Construct the trust policy using the OIDC provider
    assume_role_policy = pulumi.Output.all(oidc_issuer_url, oidc_provider_arn).apply(
        lambda args: json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {
                        "Federated": args[1]
                    },
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Condition": {
                        "StringEquals": {
                            f"{args[0].replace('https://', '')}:sub": f"system:serviceaccount:{sa_namespace}:{sa_name}",
                            f"{args[0].replace('https://', '')}:aud": "sts.amazonaws.com"
                        }
                    }
                }
            ]
        })
    )

    aws_lbc_role = aws.iam.Role(
        f"{cluster_name}-aws-lbc-role",
        name=f"{cluster_name}-aws-load-balancer-controller",
        assume_role_policy=assume_role_policy,
        tags=tags,
    )

    aws.iam.RolePolicyAttachment(
        f"{cluster_name}-aws-lbc-role-policy-attachment",
        role=aws_lbc_role.name,
        policy_arn=aws_lbc_policy.arn,
    )

    return {
        "role_arn": aws_lbc_role.arn,
        "role_name": aws_lbc_role.name,
    }


def create_karpenter_pod_identity(
    cluster_name: str,
    node_role_arn: pulumi.Output,
    pod_identity_agent_addon: Any,
    tags: Dict[str, str],
) -> Dict[str, Any]:
    """
    Create IAM role + EKS Pod Identity Association for Karpenter.
    Uses pods.eks.amazonaws.com trust — no OIDC annotation needed.
    """

    policy_document = node_role_arn.apply(
        lambda role_arn: json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Sid": "Karpenter",
                    "Effect": "Allow",
                    "Action": [
                        "ec2:CreateFleet",
                        "ec2:CreateLaunchTemplate",
                        "ec2:CreateTags",
                        "ec2:DeleteLaunchTemplate",
                        "ec2:DescribeAvailabilityZones",
                        "ec2:DescribeImages",
                        "ec2:DescribeInstanceTypeOfferings",
                        "ec2:DescribeInstanceTypes",
                        "ec2:DescribeInstances",
                        "ec2:DescribeLaunchTemplates",
                        "ec2:DescribeSecurityGroups",
                        "ec2:DescribeSpotPriceHistory",
                        "ec2:DescribeSubnets",
                        "ec2:DescribeVolumes",
                        "ec2:RunInstances",
                        "ec2:TerminateInstances",
                        "pricing:GetProducts",
                        "ssm:GetParameter",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "PassNodeIAMRole",
                    "Effect": "Allow",
                    "Action": "iam:PassRole",
                    "Resource": role_arn,
                },
                {
                    "Sid": "EKSClusterLookup",
                    "Effect": "Allow",
                    "Action": "eks:DescribeCluster",
                    "Resource": "*",
                },
                {
                    "Sid": "InstanceProfileActions",
                    "Effect": "Allow",
                    "Action": [
                        "iam:CreateInstanceProfile",
                        "iam:DeleteInstanceProfile",
                        "iam:GetInstanceProfile",
                        "iam:AddRoleToInstanceProfile",
                        "iam:RemoveRoleFromInstanceProfile",
                        "iam:TagInstanceProfile",
                    ],
                    "Resource": "*",
                },
                {
                    "Sid": "SQSInterruption",
                    "Effect": "Allow",
                    "Action": [
                        "sqs:DeleteMessage",
                        "sqs:GetQueueAttributes",
                        "sqs:GetQueueUrl",
                        "sqs:ReceiveMessage",
                    ],
                    "Resource": "*",
                },
            ],
        })
    )

    karpenter_policy = aws.iam.Policy(
        f"{cluster_name}-karpenter-controller-policy",
        name=f"{cluster_name}-karpenter-controller",
        policy=policy_document,
        tags=tags,
    )

    karpenter_role = aws.iam.Role(
        f"{cluster_name}-karpenter-controller-role",
        name=f"{cluster_name}-karpenter-controller",
        assume_role_policy=json.dumps({
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "pods.eks.amazonaws.com"},
                    "Action": ["sts:AssumeRole", "sts:TagSession"],
                }
            ],
        }),
        tags=tags,
    )

    aws.iam.RolePolicyAttachment(
        f"{cluster_name}-karpenter-controller-role-policy-attachment",
        role=karpenter_role.name,
        policy_arn=karpenter_policy.arn,
    )

    aws.eks.PodIdentityAssociation(
        f"{cluster_name}-karpenter-pod-identity",
        cluster_name=cluster_name,
        namespace="kube-system",
        service_account="karpenter",
        role_arn=karpenter_role.arn,
        tags=tags,
        opts=pulumi.ResourceOptions(depends_on=[pod_identity_agent_addon]),
    )

    return {
        "role_arn": karpenter_role.arn,
        "role_name": karpenter_role.name,
    }

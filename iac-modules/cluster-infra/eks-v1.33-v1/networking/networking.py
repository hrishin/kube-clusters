"""
Networking infrastructure for EKS cluster
Equivalent to terraform/modules/networking
"""

import pulumi
import pulumi_aws as aws
from typing import Dict, Any, List


def create_networking(
    cluster_name: str,
    vpc_cidr: str,
    availability_zones: List[str],
    tags: Dict[str, str],
) -> Dict[str, Any]:
    """
    Create VPC, subnets, NAT gateways, and security groups
    
    Args:
        cluster_name: Name of the EKS cluster
        vpc_cidr: CIDR block for the VPC
        availability_zones: List of availability zones to use
        tags: Common tags to apply to resources
        
    Returns:
        Dictionary containing networking resource outputs
    """
    
    # Create VPC
    vpc = aws.ec2.Vpc(
        f"{cluster_name}-vpc",
        cidr_block=vpc_cidr,
        enable_dns_hostnames=True,
        enable_dns_support=True,
        tags={
            **tags,
            "Name": f"{cluster_name}-vpc",
            f"kubernetes.io/cluster/{cluster_name}": "shared",
        },
    )
    
    # Create Internet Gateway
    igw = aws.ec2.InternetGateway(
        f"{cluster_name}-igw",
        vpc_id=vpc.id,
        tags={
            **tags,
            "Name": f"{cluster_name}-igw",
        },
    )
    
    # Create Public Subnets
    public_subnets = []
    for i, az in enumerate(availability_zones):
        subnet = aws.ec2.Subnet(
            f"{cluster_name}-public-subnet-{i}",
            vpc_id=vpc.id,
            cidr_block=f"10.0.{i+1}.0/24",  # Equivalent to cidrsubnet(vpc_cidr, 8, i+1)
            availability_zone=az,
            map_public_ip_on_launch=True,
            tags={
                **tags,
                "Name": f"{cluster_name}-public-subnet-{i}",
                f"kubernetes.io/cluster/{cluster_name}": "shared",
                "kubernetes.io/role/elb": "1",
            },
        )
        public_subnets.append(subnet)
    
    # Create Private Subnets
    private_subnets = []
    for i, az in enumerate(availability_zones):
        subnet = aws.ec2.Subnet(
            f"{cluster_name}-private-subnet-{i}",
            vpc_id=vpc.id,
            cidr_block=f"10.0.{i+10}.0/24",  # Equivalent to cidrsubnet(vpc_cidr, 8, i+10)
            availability_zone=az,
            tags={
                **tags,
                "Name": f"{cluster_name}-private-subnet-{i}",
                f"kubernetes.io/cluster/{cluster_name}": "owned",
                "kubernetes.io/role/internal-elb": "1",
                "karpenter.sh/discovery": cluster_name,
            },
        )
        private_subnets.append(subnet)
    
    # Create Elastic IPs for NAT Gateways
    eips = []
    for i in range(len(public_subnets)):
        eip = aws.ec2.Eip(
            f"{cluster_name}-nat-eip-{i}",
            domain="vpc",
            tags={
                **tags,
                "Name": f"{cluster_name}-nat-eip-{i}",
            },
            opts=pulumi.ResourceOptions(depends_on=[igw]),
        )
        eips.append(eip)
    
    # Create NAT Gateways
    nat_gateways = []
    for i, (public_subnet, eip) in enumerate(zip(public_subnets, eips)):
        nat_gw = aws.ec2.NatGateway(
            f"{cluster_name}-nat-gw-{i}",
            allocation_id=eip.id,
            subnet_id=public_subnet.id,
            tags={
                **tags,
                "Name": f"{cluster_name}-nat-gw-{i}",
            },
            opts=pulumi.ResourceOptions(depends_on=[igw]),
        )
        nat_gateways.append(nat_gw)
    
    # Create Route Table for Public Subnets
    public_route_table = aws.ec2.RouteTable(
        f"{cluster_name}-public-rt",
        vpc_id=vpc.id,
        routes=[
            aws.ec2.RouteTableRouteArgs(
                cidr_block="0.0.0.0/0",
                gateway_id=igw.id,
            ),
        ],
        tags={
            **tags,
            "Name": f"{cluster_name}-public-rt",
        },
    )
    
    # Associate Public Subnets with Public Route Table
    for i, subnet in enumerate(public_subnets):
        aws.ec2.RouteTableAssociation(
            f"{cluster_name}-public-rta-{i}",
            subnet_id=subnet.id,
            route_table_id=public_route_table.id,
        )
    
    # Create Route Tables for Private Subnets (one per subnet for NAT)
    private_route_tables = []
    for i, (subnet, nat_gw) in enumerate(zip(private_subnets, nat_gateways)):
        route_table = aws.ec2.RouteTable(
            f"{cluster_name}-private-rt-{i}",
            vpc_id=vpc.id,
            routes=[
                aws.ec2.RouteTableRouteArgs(
                    cidr_block="0.0.0.0/0",
                    nat_gateway_id=nat_gw.id,
                ),
            ],
            tags={
                **tags,
                "Name": f"{cluster_name}-private-rt-{i}",
            },
        )
        private_route_tables.append(route_table)
        
        # Associate Private Subnet with its Route Table
        aws.ec2.RouteTableAssociation(
            f"{cluster_name}-private-rta-{i}",
            subnet_id=subnet.id,
            route_table_id=route_table.id,
        )
    
    # Create Worker Node Security Group
    worker_sg = aws.ec2.SecurityGroup(
        f"{cluster_name}-worker-nodes-sg",
        name_prefix=f"{cluster_name}-worker-nodes-",
        description="Security group for EKS worker nodes",
        vpc_id=vpc.id,
        ingress=[
            aws.ec2.SecurityGroupIngressArgs(
                description="SSH access",
                from_port=22,
                to_port=22,
                protocol="tcp",
                cidr_blocks=["0.0.0.0/0"],
            ),
            aws.ec2.SecurityGroupIngressArgs(
                description="All traffic between worker nodes",
                from_port=0,
                to_port=0,
                protocol="-1",
                self=True,
            ),
        ],
        egress=[
            aws.ec2.SecurityGroupEgressArgs(
                description="All outbound traffic",
                from_port=0,
                to_port=0,
                protocol="-1",
                cidr_blocks=["0.0.0.0/0"],
            ),
        ],
        tags={
            **tags,
            "Name": f"{cluster_name}-worker-nodes-sg",
            f"kubernetes.io/cluster/{cluster_name}": "owned",
            "karpenter.sh/discovery": cluster_name,
        },
    )
    
    # --- Pre-provisioned NLB for Ingress ---

    # Create NLB Security Group
    nlb_sg = aws.ec2.SecurityGroup(
        f"{cluster_name}-nlb-sg",
        name_prefix=f"{cluster_name}-nlb-",
        description="Security group for shared cluster NLB",
        vpc_id=vpc.id,
        ingress=[
            aws.ec2.SecurityGroupIngressArgs(
                description="HTTP access",
                from_port=80,
                to_port=80,
                protocol="tcp",
                cidr_blocks=["0.0.0.0/0"],
            ),
            aws.ec2.SecurityGroupIngressArgs(
                description="HTTPS access",
                from_port=443,
                to_port=443,
                protocol="tcp",
                cidr_blocks=["0.0.0.0/0"],
            ),
            aws.ec2.SecurityGroupIngressArgs(
                description="HTTP node port",
                from_port=3080,
                to_port=3080,
                protocol="tcp",
                cidr_blocks=["0.0.0.0/0"],
            ),
        ],
        egress=[
            aws.ec2.SecurityGroupEgressArgs(
                description="Forward to gateway HTTP node port",
                from_port=3080,
                to_port=3080,
                protocol="tcp",
                cidr_blocks=["0.0.0.0/0"],
            ),
            aws.ec2.SecurityGroupEgressArgs(
                description="Forward to gateway HTTPS node port",
                from_port=30443,
                to_port=30443,
                protocol="tcp",
                cidr_blocks=["0.0.0.0/0"],
            ),
        ],
        tags={
            **tags,
            "Name": f"{cluster_name}-nlb-sg",
        },
    )

    # Allow NLB to communicate with worker nodes (required for Target Group routing)
    aws.ec2.SecurityGroupRule(
        f"{cluster_name}-nlb-to-workers",
        type="ingress",
        from_port=0,
        to_port=65535,
        protocol="tcp",
        source_security_group_id=nlb_sg.id,
        security_group_id=worker_sg.id,
        description="Allow NLB to access worker pods/nodeports",
    )

    nlb = aws.lb.LoadBalancer(
        f"{cluster_name}-nlb",
        internal=False,
        load_balancer_type="network",
        security_groups=[nlb_sg.id],
        subnets=[s.id for s in public_subnets],
        enable_deletion_protection=False,
        tags={
            **tags,
            "Name": f"{cluster_name}-nlb",
        },
    )

    # Port 80 Target Group
    target_group_80 = aws.lb.TargetGroup(
        f"{cluster_name}-tg-80",
        port=3080,
        protocol="TCP",
        target_type="instance", # Routes traffic to EC2 nodes
        vpc_id=vpc.id,
        health_check=aws.lb.TargetGroupHealthCheckArgs(
            protocol="TCP",
            port="3080",
            healthy_threshold=2,
            unhealthy_threshold=2,
            timeout=3,
            interval=10,
        ),
        tags={
            **tags,
            "Name": f"{cluster_name}-tg-80",
        },
    )

    # Port 443 Target Group
    target_group_443 = aws.lb.TargetGroup(
        f"{cluster_name}-tg-443",
        port=30443,
        protocol="TCP",
        target_type="instance", # Routes traffic to EC2 nodes
        vpc_id=vpc.id,
        health_check=aws.lb.TargetGroupHealthCheckArgs(
            protocol="TCP",
            port="30443",
            healthy_threshold=2,
            unhealthy_threshold=2,
            timeout=3,
            interval=10,
        ),
        tags={
            **tags,
            "Name": f"{cluster_name}-tg-443",
        },
    )

    # Create TCP Listeners forwarding to Target Groups
    tcp_listener_80 = aws.lb.Listener(
        f"{cluster_name}-listener-80",
        load_balancer_arn=nlb.arn,
        port=3080,
        protocol="TCP",
        default_actions=[
            aws.lb.ListenerDefaultActionArgs(
                type="forward",
                target_group_arn=target_group_80.arn,
            )
        ],
    )

    tcp_listener_443 = aws.lb.Listener(
        f"{cluster_name}-listener-443",
        load_balancer_arn=nlb.arn,
        port=443,
        protocol="TCP",
        default_actions=[
            aws.lb.ListenerDefaultActionArgs(
                type="forward",
                target_group_arn=target_group_443.arn,
            )
        ],
    )

    return {
        "vpc_id": vpc.id,
        "vpc_cidr_block": vpc.cidr_block,
        "public_subnet_ids": [s.id for s in public_subnets],
        "private_subnet_ids": [s.id for s in private_subnets],
        "internet_gateway_id": igw.id,
        "nat_gateway_ids": [ng.id for ng in nat_gateways],
        "worker_node_security_group_id": worker_sg.id,
        "nlb_arn": nlb.arn,
        "nlb_dns_name": nlb.dns_name,
        "target_group_arn_80": target_group_80.arn,
        "target_group_arn_443": target_group_443.arn,
    }


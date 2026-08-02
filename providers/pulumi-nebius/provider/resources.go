package provider

import (
	"github.com/pulumi/pulumi-terraform-bridge/v3/pkg/pf/tfbridge"
	pfbridge "github.com/pulumi/pulumi-terraform-bridge/v3/pkg/pf/tfbridge"
	tfbridgev3 "github.com/pulumi/pulumi-terraform-bridge/v3/pkg/tfbridge"

	nebius "github.com/nebius/terraform-provider-nebius/internal/provider"
)

const (
	ProviderName    = "nebius"
	ProviderVersion = "0.6.35"
)

func Provider() tfbridgev3.ProviderInfo {
	p := pfbridge.ShimProviderWithContext(nebius.New())

	return tfbridgev3.ProviderInfo{
		P:           p,
		Name:        ProviderName,
		Version:     ProviderVersion,
		Description: "A Pulumi provider for managing Nebius AI Cloud infrastructure.",
		Publisher:   "hrishin",
		Repository:  "https://github.com/hrishin/pulumi-nebius",
		Keywords:    []string{"pulumi", "nebius", "kubernetes", "gpu", "ai"},
		License:     "Apache-2.0",
		LogoURL:     "https://nebius.com/logo.png",

		Config: map[string]*tfbridgev3.SchemaInfo{
			"domain":     {Name: "domain"},
			"token":      {Name: "token", Secret: tfbridgev3.True()},
			"module_name": {Name: "moduleName"},
		},

		Resources: map[string]*tfbridgev3.ResourceInfo{
			// MK8S — Managed Kubernetes
			"nebius_mk8s_v1_cluster":       {Tok: tfbridge.MakeResource(ProviderName, "mk8s", "Cluster")},
			"nebius_mk8s_v1_node_group":    {Tok: tfbridge.MakeResource(ProviderName, "mk8s", "NodeGroup")},
			"nebius_mk8s_v1alpha1_cluster":  {Tok: tfbridge.MakeResource(ProviderName, "mk8s", "ClusterAlpha")},
			"nebius_mk8s_v1alpha1_node_group": {Tok: tfbridge.MakeResource(ProviderName, "mk8s", "NodeGroupAlpha")},

			// VPC — Networking
			"nebius_vpc_v1_network":        {Tok: tfbridge.MakeResource(ProviderName, "vpc", "Network")},
			"nebius_vpc_v1_subnet":         {Tok: tfbridge.MakeResource(ProviderName, "vpc", "Subnet")},
			"nebius_vpc_v1_security_group": {Tok: tfbridge.MakeResource(ProviderName, "vpc", "SecurityGroup")},
			"nebius_vpc_v1_security_rule":  {Tok: tfbridge.MakeResource(ProviderName, "vpc", "SecurityRule")},
			"nebius_vpc_v1_allocation":     {Tok: tfbridge.MakeResource(ProviderName, "vpc", "Allocation")},
			"nebius_vpc_v1_route_table":    {Tok: tfbridge.MakeResource(ProviderName, "vpc", "RouteTable")},
			"nebius_vpc_v1_route":          {Tok: tfbridge.MakeResource(ProviderName, "vpc", "Route")},
			"nebius_vpc_v1_pool":           {Tok: tfbridge.MakeResource(ProviderName, "vpc", "Pool")},
			"nebius_vpc_v1alpha1_allocation": {Tok: tfbridge.MakeResource(ProviderName, "vpc", "AllocationAlpha")},
			"nebius_tunnel_v1_tunnel":      {Tok: tfbridge.MakeResource(ProviderName, "vpc", "Tunnel")},

			// Compute
			"nebius_compute_v1_disk":             {Tok: tfbridge.MakeResource(ProviderName, "compute", "Disk")},
			"nebius_compute_v1_disk_snapshot":    {Tok: tfbridge.MakeResource(ProviderName, "compute", "DiskSnapshot")},
			"nebius_compute_v1_filesystem":       {Tok: tfbridge.MakeResource(ProviderName, "compute", "Filesystem")},
			"nebius_compute_v1_gpu_cluster":      {Tok: tfbridge.MakeResource(ProviderName, "compute", "GpuCluster")},
			"nebius_compute_v1_instance":         {Tok: tfbridge.MakeResource(ProviderName, "compute", "Instance")},
			"nebius_compute_v1_nvl_instance_group": {Tok: tfbridge.MakeResource(ProviderName, "compute", "NvlInstanceGroup")},

			// IAM
			"nebius_iam_v1_service_account":      {Tok: tfbridge.MakeResource(ProviderName, "iam", "ServiceAccount")},
			"nebius_iam_v1_access_permit":        {Tok: tfbridge.MakeResource(ProviderName, "iam", "AccessPermit")},
			"nebius_iam_v1_auth_public_key":      {Tok: tfbridge.MakeResource(ProviderName, "iam", "AuthPublicKey")},
			"nebius_iam_v1_group":                {Tok: tfbridge.MakeResource(ProviderName, "iam", "Group")},
			"nebius_iam_v1_group_membership":     {Tok: tfbridge.MakeResource(ProviderName, "iam", "GroupMembership")},
			"nebius_iam_v1_federation":           {Tok: tfbridge.MakeResource(ProviderName, "iam", "Federation")},
			"nebius_iam_v1_federation_certificate": {Tok: tfbridge.MakeResource(ProviderName, "iam", "FederationCertificate")},
			"nebius_iam_v1_federated_credentials": {Tok: tfbridge.MakeResource(ProviderName, "iam", "FederatedCredentials")},
			"nebius_iam_v1_invitation":           {Tok: tfbridge.MakeResource(ProviderName, "iam", "Invitation")},
			"nebius_iam_v2_access_key":           {Tok: tfbridge.MakeResource(ProviderName, "iam", "AccessKey")},
			"nebius_iam_v2_project":              {Tok: tfbridge.MakeResource(ProviderName, "iam", "Project")},

			// KMS
			"nebius_kms_v1_symmetric_key":  {Tok: tfbridge.MakeResource(ProviderName, "kms", "SymmetricKey")},
			"nebius_kms_v1_asymmetric_key": {Tok: tfbridge.MakeResource(ProviderName, "kms", "AsymmetricKey")},

			// Storage
			"nebius_storage_v1_bucket":          {Tok: tfbridge.MakeResource(ProviderName, "storage", "Bucket")},
			"nebius_storage_v1_transfer":        {Tok: tfbridge.MakeResource(ProviderName, "storage", "Transfer")},
			"nebius_storage_v1alpha1_transfer":  {Tok: tfbridge.MakeResource(ProviderName, "storage", "TransferAlpha")},

			// DNS
			"nebius_dns_v1_zone":   {Tok: tfbridge.MakeResource(ProviderName, "dns", "Zone")},
			"nebius_dns_v1_record": {Tok: tfbridge.MakeResource(ProviderName, "dns", "Record")},

			// Registry
			"nebius_registry_v1_registry": {Tok: tfbridge.MakeResource(ProviderName, "registry", "Registry")},

			// MSP
			"nebius_msp_mlflow_v1alpha1_cluster":      {Tok: tfbridge.MakeResource(ProviderName, "msp", "MlflowCluster")},
			"nebius_msp_postgresql_v1alpha1_cluster":  {Tok: tfbridge.MakeResource(ProviderName, "msp", "PostgresqlCluster")},

			// Secrets
			"nebius_mysterybox_v1_secret":         {Tok: tfbridge.MakeResource(ProviderName, "secrets", "Secret")},
			"nebius_mysterybox_v1_secret_version": {Tok: tfbridge.MakeResource(ProviderName, "secrets", "SecretVersion")},

			// Quotas / Capacity
			"nebius_quotas_v1_quota_allowance":      {Tok: tfbridge.MakeResource(ProviderName, "quotas", "QuotaAllowance")},
			"nebius_capacity_v1_capacity_allowance": {Tok: tfbridge.MakeResource(ProviderName, "capacity", "CapacityAllowance")},

			// Applications
			"nebius_applications_v1alpha1_k8s_release": {Tok: tfbridge.MakeResource(ProviderName, "applications", "K8sRelease")},
		},

		DataSources: map[string]*tfbridgev3.DataSourceInfo{},

		JavaScript: &tfbridgev3.JavaScriptInfo{
			PackageName: "@pulumi/nebius",
		},
		Python: &tfbridgev3.PythonInfo{
			PackageName: "pulumi_nebius",
			Requires: map[string]string{
				"pulumi": ">=3.0.0,<4.0.0",
			},
		},
		Golang: &tfbridgev3.GolangInfo{
			ImportBasePath:               "github.com/hrishin/pulumi-nebius/sdk/go/nebius",
			GenerateResourceContainerTypes: true,
		},
	}
}

package main

import (
	"github.com/hrishin/pulumi-nebius/provider"
	tfbridge "github.com/pulumi/pulumi-terraform-bridge/v3/pkg/tfbridge"
)

func main() {
	tfbridge.Main("nebius", provider.ProviderVersion, provider.Provider(), pulumiSchema)
}

//go:generate go run github.com/pulumi/pulumi-terraform-bridge/v3/cmd/pulumi-tfgen-nebius
var pulumiSchema []byte

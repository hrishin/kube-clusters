// pulumi-tfgen-nebius generates schema.json by introspecting the Nebius
// Terraform provider via the bridge. Run with:
//
//	pulumi-tfgen-nebius all --out sdk/
//
// This produces sdk/schema.json which is then used by:
//
//	pulumi package gen-sdk --language python sdk/schema.json --out sdk/python
package main

import (
	"github.com/hrishin/pulumi-nebius/provider"
	tfgen "github.com/pulumi/pulumi-terraform-bridge/v3/pkg/tfgen"
)

func main() {
	tfgen.Main("nebius", provider.ProviderVersion, provider.Provider())
}

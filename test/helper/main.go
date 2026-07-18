package main

import (
	"encoding/json"
	"fmt"
	"os"

	"github.com/tailscale/hujson"
)

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintln(os.Stderr, "usage: helper length <policy> <key> | validate <jsonc>")
		os.Exit(2)
	}
	switch os.Args[1] {
	case "length":
		b, err := os.ReadFile(os.Args[2])
		if err != nil {
			panic(err)
		}
		var v map[string]any
		if err := json.Unmarshal(b, &v); err != nil {
			panic(err)
		}
		filesystem := v["filesystem"].(map[string]any)
		fmt.Println(len(filesystem[os.Args[3]].([]any)))
	case "validate":
		b, err := os.ReadFile(os.Args[2])
		if err == nil {
			_, err = hujson.Parse(b)
		}
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	default:
		fmt.Fprintf(os.Stderr, "unknown helper command: %s\n", os.Args[1])
		os.Exit(2)
	}
}

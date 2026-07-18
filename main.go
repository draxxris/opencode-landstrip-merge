package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/tailscale/hujson"
)

var externalSources = []string{"allowRead", "allowWrite"}

const defaultBaseline = "~/.config/opencode/landstrip.json"

type projectRuleSet struct {
	allowRead  []string
	allowWrite []string
	denyWrite  []string
	exists     bool
}

func die(format string, args ...any) error {
	return fmt.Errorf("opencode-landstrip-merge: "+format, args...)
}

func expandHome(path string) string {
	if path == "~" || strings.HasPrefix(path, "~/") {
		home, _ := os.UserHomeDir()
		return filepath.Join(home, strings.TrimPrefix(path, "~/"))
	}
	return path
}

func readJSON(path string) (map[string]any, error) {
	b, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var v any
	if err := json.Unmarshal(b, &v); err != nil {
		return nil, die("%s is not valid JSON: %v", path, err)
	}
	m, ok := v.(map[string]any)
	if !ok {
		return nil, die("%s must contain a JSON object, got %T", path, v)
	}
	return m, nil
}

func projectRules(path string) (projectRuleSet, error) {
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		return projectRuleSet{}, nil
	}
	if err != nil {
		return projectRuleSet{}, err
	}
	var raw any
	if err := json.Unmarshal(b, &raw); err != nil {
		return projectRuleSet{}, die("%s is not valid JSON: %v", path, err)
	}
	data, ok := raw.(map[string]any)
	if !ok {
		return projectRuleSet{}, die("%s must contain a JSON object, got %T", path, raw)
	}
	read, err := stringList(data["allowRead"], "allowRead", path)
	if err != nil {
		return projectRuleSet{}, err
	}
	write, err := stringList(data["allowWrite"], "allowWrite", path)
	if err != nil {
		return projectRuleSet{}, err
	}
	denyWrite, err := stringList(data["denyWrite"], "denyWrite", path)
	if err != nil {
		return projectRuleSet{}, err
	}
	return projectRuleSet{
		allowRead:  read,
		allowWrite: write,
		denyWrite:  denyWrite,
		exists:     true,
	}, nil
}

func stringList(v any, key, path string) ([]string, error) {
	if v == nil {
		return []string{}, nil
	}
	if s, ok := v.(string); ok {
		return []string{s}, nil
	}
	a, ok := v.([]any)
	if !ok {
		return nil, die("'%s' in %s must be a list, got %T", key, path, v)
	}
	out := make([]string, len(a))
	for i, x := range a {
		out[i] = fmt.Sprint(x)
	}
	return out, nil
}

func mergePolicy(base map[string]any, rules projectRuleSet) map[string]any {
	// JSON round-trip gives us a deep copy without imposing a schema on the baseline.
	b, _ := json.Marshal(base)
	var merged map[string]any
	_ = json.Unmarshal(b, &merged)
	fs, ok := merged["filesystem"].(map[string]any)
	if !ok {
		fs = map[string]any{}
		merged["filesystem"] = fs
	}
	for key, extra := range map[string][]string{
		"allowRead":  rules.allowRead,
		"allowWrite": rules.allowWrite,
		"denyWrite":  rules.denyWrite,
	} {
		if key == "denyWrite" && len(extra) == 0 {
			if _, present := fs[key]; !present {
				continue
			}
		}
		v, _ := fs[key].([]any)
		seen := map[string]bool{}
		for _, x := range v {
			if s, ok := x.(string); ok {
				seen[s] = true
			}
		}
		for _, p := range extra {
			if !seen[p] {
				v = append(v, p)
				seen[p] = true
			}
		}
		fs[key] = v
	}
	return merged
}

func glob(path string) string {
	p := expandHome(path)
	if strings.HasSuffix(p, "**") || strings.HasSuffix(p, "*") {
		return p
	}
	if strings.HasSuffix(p, "/") {
		return p + "**"
	}
	return p + "/**"
}
func globBase(s string) string {
	s = strings.TrimRight(s, "/")
	s = strings.TrimSuffix(s, "/**")
	s = strings.TrimSuffix(s, "/*")
	return strings.TrimRight(s, "/")
}

func derived(read, write []string) []string {
	seen := map[string]bool{}
	var out []string
	for _, list := range [][]string{read, write} {
		for _, p := range list {
			g := glob(p)
			b := globBase(g)
			if !seen[b] {
				seen[b] = true
				out = append(out, g)
			}
		}
	}
	return out
}

func huString(s string) hujson.Value {
	return hujson.Value{Value: hujson.Literal([]byte(strconvQuote(s)))}
}
func strconvQuote(s string) string { b, _ := json.Marshal(s); return string(b) }
func valueObject(entries []string, indent string) *hujson.Object {
	o := &hujson.Object{}
	for _, key := range entries {
		o.Members = append(o.Members, hujson.ObjectMember{Name: huString(key), Value: huString("allow")})
	}
	for i := range o.Members {
		o.Members[i].Name.BeforeExtra = hujson.Extra("\n" + indent + "    ")
		o.Members[i].Value.BeforeExtra = hujson.Extra(" ")
	}
	o.AfterExtra = hujson.Extra("\n" + indent)
	return o
}

func objectMember(key string, value hujson.Value, before string) hujson.ObjectMember {
	m := hujson.ObjectMember{Name: huString(key), Value: value}
	m.Name.BeforeExtra = hujson.Extra(before)
	m.Value.BeforeExtra = hujson.Extra(" ")
	return m
}

func objectChild(o *hujson.Object, key string) *hujson.Value {
	for i := range o.Members {
		if string(o.Members[i].Name.Value.(hujson.Literal)) == strconvQuote(key) {
			return &o.Members[i].Value
		}
	}
	return nil
}
func appendEntries(o *hujson.Object, entries []string) bool {
	seen := map[string]bool{}
	for _, m := range o.Members {
		if l, ok := m.Name.Value.(hujson.Literal); ok {
			var s string
			_ = json.Unmarshal(l, &s)
			seen[globBase(s)] = true
		}
	}
	indent := "    "
	if len(o.Members) > 0 {
		indent = string(o.Members[0].Name.BeforeExtra)
		indent = strings.TrimPrefix(indent, "\n")
		indent = strings.TrimSpace(indent)
	}
	changed := false
	for _, key := range entries {
		if seen[globBase(key)] {
			continue
		}
		m := hujson.ObjectMember{Name: huString(key), Value: huString("allow")}
		m.Name.BeforeExtra = hujson.Extra("\n" + indent)
		m.Value.BeforeExtra = hujson.Extra(" ")
		o.Members = append(o.Members, m)
		seen[globBase(key)] = true
		changed = true
	}
	return changed
}

func mirror(path string, entries []string) error {
	if len(entries) == 0 {
		return nil
	}
	b, err := os.ReadFile(path)
	if errors.Is(err, os.ErrNotExist) {
		ed := valueObject(entries, "        ")
		perm := &hujson.Object{Members: []hujson.ObjectMember{
			objectMember("external_directory", hujson.Value{Value: ed}, "\n        "),
		}, AfterExtra: hujson.Extra("\n    ")}
		rootObj := &hujson.Object{Members: []hujson.ObjectMember{
			objectMember("permission", hujson.Value{Value: perm}, "\n    "),
		}, AfterExtra: hujson.Extra("\n")}
		root := hujson.Value{Value: rootObj}
		return os.WriteFile(path, root.Pack(), 0644)
	}
	if err != nil {
		return err
	}
	root, err := hujson.Parse(b)
	if err != nil {
		return die("%s is not valid JSONC: %v", path, err)
	}
	ro, ok := root.Value.(*hujson.Object)
	if !ok {
		return die("%s must contain a JSON object", path)
	}
	permVal := objectChild(ro, "permission")
	if permVal == nil {
		m := hujson.ObjectMember{Name: huString("permission"), Value: hujson.Value{Value: &hujson.Object{}}}
		ro.Members = append([]hujson.ObjectMember{m}, ro.Members...)
		permVal = &ro.Members[0].Value
	}
	perm, ok := permVal.Value.(*hujson.Object)
	if !ok {
		return die("permission in %s must be an object", path)
	}
	edVal := objectChild(perm, "external_directory")
	if edVal == nil {
		m := hujson.ObjectMember{Name: huString("external_directory"), Value: hujson.Value{Value: valueObject(nil, "        ")}}
		perm.Members = append(perm.Members, m)
		edVal = &perm.Members[len(perm.Members)-1].Value
	}
	ed, ok := edVal.Value.(*hujson.Object)
	if !ok {
		return die("external_directory in %s must be an object", path)
	}
	if !appendEntries(ed, entries) {
		return nil
	}
	return os.WriteFile(path, root.Pack(), 0644)
}

func main() {
	baseline := flag.String("baseline", os.Getenv("OC_LANDSTRIP_BASELINE"), "baseline policy")
	rules := flag.String("rules", ".opencode/landstrip.json", "project rules")
	jsonc := flag.String("jsonc", "./opencode.jsonc", "opencode JSONC")
	out := flag.String("out", "/tmp/opencode-scratch/landstrip-policy.json", "output policy")
	noJSONC := flag.Bool("no-jsonc", false, "skip JSONC mirroring")
	verbose := flag.Bool("verbose", false, "verbose")
	flag.Parse()
	if *baseline == "" {
		*baseline = defaultBaseline
	}
	basePath := expandHome(*baseline)
	if _, err := os.Stat(basePath); err != nil {
		fmt.Fprintf(os.Stderr, "opencode-landstrip-merge: baseline policy not found: %s\n", basePath)
		if *baseline == defaultBaseline {
			fmt.Fprintln(os.Stderr, "  (run `mise run install` to create the default baseline)")
		}
		os.Exit(1)
	}
	base, err := readJSON(basePath)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	project, err := projectRules(*rules)
	if err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if !project.exists {
		project = projectRuleSet{}
		if *verbose {
			fmt.Fprintf(os.Stderr, "opencode-landstrip-merge: no %s found; using baseline policy only\n", *rules)
		}
	}
	b, _ := json.MarshalIndent(mergePolicy(base, project), "", "    ")
	b = append(b, '\n')
	if err := os.MkdirAll(filepath.Dir(*out), 0755); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if err := os.WriteFile(*out, b, 0644); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
	if !*noJSONC {
		if err := mirror(*jsonc, derived(project.allowRead, project.allowWrite)); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(2)
		}
	}
	fmt.Println(*out)
}

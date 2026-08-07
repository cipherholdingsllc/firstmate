# cmux war room

A war room is one cmux workspace laid out as a multi-pane grid so an operator can see a lead and its workers side by side, at a glance, instead of hunting through sidebar tabs.
This page gives concrete command recipes for building, labeling, observing, and safely tearing one down.
It is an operator recipe sheet, not a backend change: [`cmux-backend.md`](cmux-backend.md) remains the single owner of Firstmate's cmux task-spawn contract.

## The 1:1 invariant is unchanged

Firstmate's supervised crewmate spawn keeps one cmux workspace with one surface per task, exactly as documented in [`cmux-backend.md`](cmux-backend.md#task-shape-and-metadata).
Nothing on this page changes that default, and none of it runs inside `bin/fm-spawn.sh` or the recovery path.
A war room is a manual, operator-driven session for watching several already-running panes at once, built with the same `cmux` CLI Firstmate already depends on.
`bin/fm-cmux-war-room.sh` is a thin standalone helper for that manual session; it is never invoked by spawn or recovery.
Flat agent-to-agent messaging is intentionally out of scope; crewmate communication flows through firstmate under the hard rule in `AGENTS.md`.

## Prerequisites

Same as [`cmux-backend.md`](cmux-backend.md#setup): cmux 0.64 or newer, `jq`, and a socket control mode of `automation` or `password`.
`bin/fm-cmux-war-room.sh` resolves the `cmux` binary the same way the adapter does: prefer `PATH`, otherwise fall back to `/Applications/cmux.app/Contents/Resources/bin/cmux`.

## Build the grid

Capture refs with a before/after `cmux tree --all --json` diff.
`new-workspace` does not provide a JSON response on the supported cmux CLI, so never pass it an unverified `--json` flag.

Stepwise splits:

```sh
before=$(mktemp)
after=$(mktemp)
cmux tree --all --json >"$before"
cmux new-workspace --name war-room --cwd "$PWD" --focus false
cmux tree --all --json >"$after"
WS=$(jq -r --slurpfile before "$before" --slurpfile after "$after" '
  ([ $after[0].windows[].workspaces[].id ] - [ $before[0].windows[].workspaces[].id ])
  | .[0] // empty')
rm -f "$before" "$after"
[ -n "$WS" ] || { echo "war-room: failed to capture non-empty workspace id" >&2; exit 1; }
cmux workspace-action --action set-color --workspace "$WS" --color Purple
A=$(cmux list-panes --workspace "$WS" --json --id-format both | jq -r '.panes[0].surface_ids[0] // .panes[0].surface_refs[0]')
[ -n "$A" ] || { echo "war-room: failed to capture non-empty lead surface ref" >&2; exit 1; }

before=$(cmux tree --workspace "$WS" --json)
cmux new-split right --workspace "$WS" --surface "$A"
after=$(cmux tree --workspace "$WS" --json)
B=$(jq -r --argjson before "$before" --argjson after "$after" '[$after.windows[].workspaces[].panes[].surfaces[]?.ref] - [$before.windows[].workspaces[].panes[].surfaces[]?.ref] | .[0] // empty')
[ -n "$B" ] || { echo "war-room: failed to capture non-empty build surface ref" >&2; exit 1; }
before=$(cmux tree --workspace "$WS" --json)
cmux new-split down --workspace "$WS" --surface "$A"
after=$(cmux tree --workspace "$WS" --json)
C=$(jq -r --argjson before "$before" --argjson after "$after" '[$after.windows[].workspaces[].panes[].surfaces[]?.ref] - [$before.windows[].workspaces[].panes[].surfaces[]?.ref] | .[0] // empty')
[ -n "$C" ] || { echo "war-room: failed to capture non-empty review surface ref" >&2; exit 1; }
before=$(cmux tree --workspace "$WS" --json)
cmux new-split down --workspace "$WS" --surface "$B"
after=$(cmux tree --workspace "$WS" --json)
D=$(jq -r --argjson before "$before" --argjson after "$after" '[$after.windows[].workspaces[].panes[].surfaces[]?.ref] - [$before.windows[].workspaces[].panes[].surfaces[]?.ref] | .[0] // empty')
[ -n "$D" ] || { echo "war-room: failed to capture non-empty fourth surface ref" >&2; exit 1; }
```

Declarative equivalent, one call instead of four:

```sh
cmux new-workspace --name war-room --cwd "$PWD" --focus false --layout '{
  "direction": "horizontal",
  "split": 0.5,
  "children": [
    {"pane": {"surfaces": [{"type": "terminal", "name": "lead"}]}},
    {"direction": "vertical", "split": 0.5, "children": [
      {"pane": {"surfaces": [{"type": "terminal", "name": "build"}]}},
      {"pane": {"surfaces": [{"type": "terminal", "name": "review"}]}}
    ]}
  ]
}'
```

Always scope splits with `--workspace` when more than one workspace exists, so a split never lands in the wrong window.

## Spatial convention

Keep the lead surface on the left and worker surfaces on the right.
The convention makes the lead easy to find while preserving a stable scan direction as workers are added.

## Label and color the fleet

Workspace color is first-class on the CLI; individual surfaces are not, so pane identity comes from the tab name and an in-pane banner instead.

```sh
cmux workspace-action --action set-color --workspace "$WS" --color Purple
cmux workspace-action --action set-description --workspace "$WS" --description "ORCH - mission-slug"
cmux rename-tab --workspace "$WS" --surface "$A" "lead"
```

`bin/fm-cmux-war-room.sh banner` wraps the tab-rename-plus-in-pane-banner pattern into one call:

```sh
bin/fm-cmux-war-room.sh banner --surface "$B" --label "A01 BUILD" --color 44
```

### Harness color usage

A local, gitignored `config/harness-visual.json` can hold a `workspace_colors` map keyed by harness name (`claude`, `codex`, `kimi`, and so on).
This file is a captain-private convention, not something Firstmate ships or reads for spawn; the war-room helper is the one place it is consulted.

```sh
bin/fm-cmux-war-room.sh color-for-harness claude
```

Prints the configured color for that harness, or `Charcoal` with a stderr note when the file or the key is absent, so a missing config never blocks the war room.
That output is a cmux workspace color name (or hex), the same value `workspace-action --action set-color` accepts, not the raw ANSI SGR number `banner --color` writes into the pane; the two commands color two different surfaces (sidebar workspace versus in-pane text) and do not share a value.

## Observe

```sh
cmux tree --all --json
cmux list-panes --workspace "$WS" --json --id-format both
cmux read-screen --workspace "$WS" --surface "$A" --scrollback --lines 80
```

`read-screen --workspace "$WS"` is deliberately scoped; never omit the workspace when reading a captured surface ref.
It returns an internal error against a genuinely fresh surface until something has been written to it; re-tree with `list-panes --workspace "$WS"` for structural readiness instead of retrying a content read.

## Capture UUIDs, never cache short refs

```sh
cmux tree --all --json
cmux list-panes --workspace "$WS" --json --id-format both
```

Short refs like `surface:3` renumber as the tree changes.
Re-run `list-panes` or `tree` before any close instead of reusing a ref captured earlier in the session.

## Tear down without stale shells

Close owned surfaces first, then the workspace; never broad-close.
`bin/fm-cmux-war-room.sh teardown-surfaces` re-reads the named workspace's own `list-panes` response so it only ever closes surfaces that workspace actually owns:

```sh
bin/fm-cmux-war-room.sh teardown-surfaces --workspace "$WS" --keep "$A" --close-workspace
```

`--keep` leaves one surface open; cmux refuses to close a workspace's last surface directly, so leaving one (or omitting `--close-workspace` and closing the workspace yourself afterward) avoids that refusal.
Every `close-surface` and `close-workspace` operation must carry `--workspace "$WS"`; an unscoped close can act on the caller's workspace.
Without `--keep`, every surface in the workspace closes, matching the [`cmux-backend.md`](cmux-backend.md#current-operation-and-safety) "last surface" and "only workspace in window" behavior that `close-workspace` already handles.

Manual equivalent, for reference:

```sh
cmux list-panes --workspace "$WS" --json --id-format both \
  | jq -r '[(.panes[]?.surface_ids[]?)] as $ids | if ($ids | length) > 0 then $ids else [(.panes[]?.surface_refs[]?)] end | unique | .[]' \
  | while read -r S; do [ -n "$S" ] || { echo "empty surface ref" >&2; exit 1; }; cmux close-surface --workspace "$WS" --surface "$S"; done
cmux close-workspace --workspace "$WS"
```

## Retire a corpse workspace

If `list-panes --workspace "$WS" --json` returns `"panes": []`, the workspace is a zero-surface corpse.
Retire only that confirmed corpse with:

```sh
bin/fm-cmux-war-room.sh retire-corpse --workspace "$WS"
```

The helper refuses to retire a workspace that still contains panes and always scopes the close to the captured workspace ref.

## Regression entry points

The colocated `tests/fm-cmux-war-room.test.sh` regression invokes teardown from a different caller workspace and asserts every close is scoped to the target.

#!/usr/bin/env bash
# Every library identifier digest/src relies on must be declared in the installed .d.ts files.
# Prints "name<TAB>file:line" or "name<TAB>MISSING" and exits 1 on any MISSING. Re-run after a
# dependency bump. Plain bash 3 (macOS /bin/bash): a here-doc list, no associative arrays.
set -uo pipefail
cd "$(dirname "$0")/.."
status=0
while IFS=$'\t' read -r name pkg; do
  [ -n "$name" ] || continue
  hit=$(grep -rn --include='*.d.ts' -m1 -w "$name" "node_modules/$pkg" 2>/dev/null | head -1 | cut -c1-140)
  if [ -n "$hit" ]; then printf '%s\t%s\n' "$name" "$hit"; else printf '%s\tMISSING\n' "$name"; status=1; fi
done <<'NAMES' | sort
query	@anthropic-ai/claude-agent-sdk
Options	@anthropic-ai/claude-agent-sdk
SDKMessage	@anthropic-ai/claude-agent-sdk
ThinkingConfig	@anthropic-ai/claude-agent-sdk
systemPrompt	@anthropic-ai/claude-agent-sdk
allowedTools	@anthropic-ai/claude-agent-sdk
disallowedTools	@anthropic-ai/claude-agent-sdk
permissionMode	@anthropic-ai/claude-agent-sdk
outputFormat	@anthropic-ai/claude-agent-sdk
structured_output	@anthropic-ai/claude-agent-sdk
total_cost_usd	@anthropic-ai/claude-agent-sdk
num_turns	@anthropic-ai/claude-agent-sdk
thinking	@anthropic-ai/claude-agent-sdk
cwd	@anthropic-ai/claude-agent-sdk
error_max_structured_output_retries	@anthropic-ai/claude-agent-sdk
WorkflowIdReusePolicy	@temporalio/common
WorkflowIdConflictPolicy	@temporalio/common
WorkflowExecutionAlreadyStartedError	@temporalio/common
ApplicationFailure	@temporalio/common
ScheduleOverlapPolicy	@temporalio/client
WorkflowStartOptions	@temporalio/client
createTimeSkipping	@temporalio/testing
nativeConnection	@temporalio/testing
runUntil	@temporalio/worker
workflowsPath	@temporalio/worker
proxyActivities	@temporalio/workflow
defineSignal	@temporalio/workflow
setHandler	@temporalio/workflow
condition	@temporalio/workflow
heartbeat	@temporalio/activity
toJSONSchema	zod
DatabaseSync	@types/node
NAMES
exit $status

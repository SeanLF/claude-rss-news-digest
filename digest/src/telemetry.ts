import { PostHog } from "posthog-node";

// PostHog, when the worker has a project token (seanfloyd-infra's tofu writes it): the model-call traces
// (runner/run-stage.ts) and each run's ending. Telemetry only: nothing a run decides reads it back.
const token = process.env["POSTHOG_PROJECT_TOKEN"];
const host = process.env["POSTHOG_HOST"];
export const posthog: PostHog | undefined = token && host ? new PostHog(token, { host }) : undefined;
if (posthog) process.once("beforeExit", () => void posthog.shutdown());

export type Track = (event: string, properties: Record<string, unknown>) => void;
export const track: Track = (event, properties) => posthog?.capture({ distinctId: "news-digest", event, properties });

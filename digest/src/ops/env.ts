import { resendBaseUrl } from "../resend/destination.js";

// One line naming what the worker's environment leaves silent, logged at startup: an alert, a ping or a
// send that cannot happen is otherwise found out only on the day it was needed. Null when all is set.
export function operationsEnvWarning(env: Record<string, string | undefined>): string | null {
  const unset = (names: string[]) => names.filter((n) => !env[n]);
  const parts: string[] = [];
  const alerts = unset(["HEALTH_ALERT_EMAIL", "RESEND_API_KEY", "RESEND_FROM"]);
  if (alerts.length) parts.push(`alerts will not be delivered (${alerts.join(", ")} unset)`);
  if (env["RESEND_API_KEY"]) {
    try {
      resendBaseUrl(env);
    } catch (e) {
      parts.push(`every email will be refused (${e instanceof Error ? e.message : String(e)})`);
    }
  }
  if (!env["HEALTHCHECK_PING_URL"]) parts.push("the dead-man's switch will not be pinged (HEALTHCHECK_PING_URL unset)");
  if (env["BROADCAST_ENABLED"] !== "true") parts.push("nothing will be broadcast (BROADCAST_ENABLED is not true)");
  else {
    const send = unset(["RESEND_API_KEY", "RESEND_FROM", "RESEND_AUDIENCE_ID"]);
    if (send.length) parts.push(`the send will fail (${send.join(", ")} unset)`);
  }
  return parts.length ? `operations misconfigured: ${parts.join("; ")}` : null;
}

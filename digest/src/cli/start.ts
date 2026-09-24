// usage: start [YYYY-MM-DD] [--force] [--resume N]   starts one DigestWorkflow and waits for its result
import { connect, parseStartArgs, startDigest } from "../client.js";
let args: ReturnType<typeof parseStartArgs>;
try {
  args = parseStartArgs(process.argv.slice(2));
} catch (e) {
  console.error(e instanceof Error ? e.message : String(e));
  process.exit(2);
}
const handle = await startDigest(await connect(), args.date, args.opts);
console.log(`started ${handle.workflowId}`);
console.log(JSON.stringify(await handle.result()));

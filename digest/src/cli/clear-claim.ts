// usage: clear-claim <YYYY-MM-DD>   in the worker container, after checking Resend that nothing went out
// Clears a send attempt's claim on the date so a resume may send. Exits 1 when there was no claim to clear.
import { clearClaim } from "../ops/broadcast-state.js";
import { dbUrl, openDb } from "../store/db.js";

const date = process.argv[2];
if (!date || !/^\d{4}-\d{2}-\d{2}$/.test(date)) throw new Error("usage: clear-claim <YYYY-MM-DD>");
const cleared = await clearClaim(openDb(dbUrl()), date);
console.log(cleared ? `cleared the send claim on ${date}` : `no claim to clear on ${date} (none held, or a broadcast id is recorded)`);
process.exit(cleared ? 0 : 1); // the pool would otherwise hold the process open

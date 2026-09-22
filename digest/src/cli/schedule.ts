// usage: schedule   creates or updates the daily digest schedule on the Temporal at TEMPORAL_ADDRESS
import { connect, ensureSchedule, SCHEDULE_ID } from "../client.js";
console.log(`${SCHEDULE_ID}: ${await ensureSchedule(await connect())}`);

// usage: migrate   applies digest/db/migrations to DIGEST_DATABASE_URL with dbmate, creating the database if needed
import { dbUrl } from "../store/db.js";
import { migrate } from "../store/schema.js";

migrate(dbUrl());
console.log("migrate: the product schema is current");

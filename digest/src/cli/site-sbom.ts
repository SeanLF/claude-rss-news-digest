// usage: site-sbom <package-lock.json> <esbuild metafile> <out.cdx.json>
// The site image ships an esbuild bundle and no node_modules, so an image scan finds no npm packages.
// Its SBOM is instead the lockfile's entries for exactly the packages the bundle's metafile says it
// contains: the versions npm installed, as CycloneDX 1.6 with npm purls.
import { readFileSync, writeFileSync } from "node:fs";

interface LockEntry {
  version?: string;
  resolved?: string;
  integrity?: string;
  license?: string;
}
interface Lock {
  name?: string;
  version?: string;
  packages: Record<string, LockEntry>;
}
interface Metafile {
  inputs: Record<string, unknown>;
}
interface Component {
  type: "library";
  "bom-ref": string;
  group?: string;
  name: string;
  version: string;
  purl: string;
  licenses?: { license: { id: string } }[] | { expression: string }[];
  hashes?: { alg: "SHA-512"; content: string }[];
}
export interface Bom {
  bomFormat: "CycloneDX";
  specVersion: "1.6";
  version: 1;
  metadata: { component: { type: "application"; name: string; version: string } };
  components: Component[];
}

// The deepest package directory an input sits in: node_modules/pg/node_modules/pg-types/index.js is
// the nested pg-types, not pg.
const PACKAGE_DIR = /^(.*node_modules\/(?:@[^/]+\/)?[^/]+)\//;

function component(dir: string, entry: LockEntry): Component {
  const full = dir.slice(dir.lastIndexOf("node_modules/") + "node_modules/".length);
  const [group, name] = full.startsWith("@") ? [full.slice(0, full.indexOf("/")), full.slice(full.indexOf("/") + 1)] : [undefined, full];
  const version = entry.version!;
  const purl = `pkg:npm/${group ? `${encodeURIComponent(group)}/` : ""}${name}@${version}`;
  const c: Component = { type: "library", "bom-ref": purl, ...(group ? { group } : {}), name, version, purl };
  if (entry.license) c.licenses = /[\s()]/.test(entry.license) ? [{ expression: entry.license }] : [{ license: { id: entry.license } }];
  if (entry.integrity?.startsWith("sha512-")) c.hashes = [{ alg: "SHA-512", content: Buffer.from(entry.integrity.slice(7), "base64").toString("hex") }];
  return c;
}

export function siteSbom(lock: Lock, meta: Metafile, name = "digest-site"): Bom {
  const dirs = new Set<string>();
  for (const input of Object.keys(meta.inputs)) {
    const m = PACKAGE_DIR.exec(input);
    if (m) dirs.add(m[1]!);
  }
  if (dirs.size === 0) throw new Error("site-sbom: the metafile names no packages; refusing to write an empty SBOM");
  const components = [...dirs].map((dir) => {
    const entry = lock.packages[dir];
    if (!entry?.version) throw new Error(`site-sbom: the bundle contains ${dir}, which the lockfile does not list`);
    return component(dir, entry);
  });
  components.sort((a, b) => (a.purl < b.purl ? -1 : 1)); // by code unit, so the order is the same everywhere
  return { bomFormat: "CycloneDX", specVersion: "1.6", version: 1, metadata: { component: { type: "application", name, version: lock.version ?? "0.0.0" } }, components };
}

if (process.argv[1]?.endsWith("site-sbom.js")) {
  const [lockPath, metaPath, out] = process.argv.slice(2);
  if (!lockPath || !metaPath || !out) {
    console.error("usage: site-sbom <package-lock.json> <metafile.json> <out.cdx.json>");
    process.exit(2);
  }
  const bom = siteSbom(JSON.parse(readFileSync(lockPath, "utf8")) as Lock, JSON.parse(readFileSync(metaPath, "utf8")) as Metafile);
  writeFileSync(out, `${JSON.stringify(bom, null, 1)}\n`);
  console.log(`site-sbom: ${bom.components.length} packages into ${out}`);
}

import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const messagesDir = join(
  dirname(fileURLToPath(import.meta.url)),
  "..",
  "frontend",
  "messages",
);

function flatten(node, prefix = "") {
  if (node === null || typeof node !== "object" || Array.isArray(node)) {
    return [prefix];
  }
  return Object.entries(node).flatMap(([key, value]) =>
    flatten(value, prefix ? `${prefix}.${key}` : key),
  );
}

const load = (locale) =>
  new Set(
    flatten(JSON.parse(readFileSync(join(messagesDir, `${locale}.json`), "utf8"))),
  );

const fr = load("fr");
const en = load("en");
const missingInEn = [...fr].filter((key) => !en.has(key));
const missingInFr = [...en].filter((key) => !fr.has(key));

if (missingInEn.length || missingInFr.length) {
  for (const key of missingInEn) console.error(`missing in en.json: ${key}`);
  for (const key of missingInFr) console.error(`missing in fr.json: ${key}`);
  process.exit(1);
}

console.log(`i18n keys check passed (${fr.size} keys).`);

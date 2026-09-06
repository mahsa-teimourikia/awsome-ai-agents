import { access, readFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
const sources = await Promise.all(
  ["app/page.tsx", "app/page-data.tsx"].map((path) =>
    readFile(resolve(root, path), "utf8"),
  ),
);
const paths = sources.flatMap((source) =>
  [...source.matchAll(/(?:notebook|example):\s*"([^"]+)"/g)].map((match) => match[1]),
);
for (const source of sources) {
  const guideBlock = source.match(/const guidePaths[^=]*=\{([\s\S]*?)\n\};/);
  if (guideBlock) paths.push(...[...guideBlock[1].matchAll(/:\s*"([^"]+)"/g)].map((match) => match[1]));
}

// The Hub also exposes local curriculum and source references in `refs` arrays.
// Extract all local source-like paths from the registry so a card cannot point
// learners at an in-repository resource that was renamed or removed.
const localReferencePattern = /curriculum\/[A-Za-z0-9_./-]+\.(?:md|ipynb)(?:#[A-Za-z0-9_-]+)?/g;
for (const source of sources) {
  paths.push(...[...source.matchAll(localReferencePattern)].map((match) => match[0].split("#")[0]));
}

const uniquePaths = [...new Set(paths)];
for (const path of uniquePaths) {
  if (/^https?:\/\//.test(path)) continue;
  try {
    await access(resolve(root, path));
  } catch {
    throw new Error(`Learning Hub link does not exist: ${path}`);
  }
}
console.log(`Validated ${uniquePaths.length} learning material links.`);

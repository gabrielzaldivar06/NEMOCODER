import fs from "node:fs";
import path from "node:path";

const targetFile = path.resolve("src/main.tsx");
const source = fs.readFileSync(targetFile, "utf8");

const buttonTags = [...source.matchAll(/<button\b[\s\S]*?>/g)].map((match) => match[0]);
const deadButtons = buttonTags.filter((tag) => !/\bonClick\s*=/.test(tag));

if (deadButtons.length > 0) {
  console.error(`Dead control gate failed: ${deadButtons.length} button(s) without onClick in ${targetFile}`);
  deadButtons.slice(0, 20).forEach((tag, index) => {
    const normalized = tag.replace(/\s+/g, " ").trim();
    console.error(`${index + 1}. ${normalized}`);
  });
  process.exit(1);
}

console.log(`Dead control gate passed: ${buttonTags.length} button(s) validated in ${targetFile}`);

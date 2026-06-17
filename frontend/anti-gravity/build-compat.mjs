import { cpSync, rmSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { execFileSync } from "node:child_process";

const here = dirname(fileURLToPath(import.meta.url));
const frontendDir = resolve(here, "../creator-chat");
const outputDir = resolve(here, "dist");
const commandOptions = {
  cwd: frontendDir,
  stdio: "inherit",
};

function runNpm(args) {
  if (process.platform === "win32") {
    execFileSync("cmd.exe", ["/d", "/s", "/c", `npm ${args.join(" ")}`], commandOptions);
    return;
  }

  execFileSync("npm", args, commandOptions);
}

runNpm(["ci"]);
runNpm(["run", "build"]);

rmSync(outputDir, { recursive: true, force: true });
cpSync(resolve(frontendDir, "dist"), outputDir, { recursive: true });

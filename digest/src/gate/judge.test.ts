import { describe, expect, it } from "vitest";
import { cliJudge, parseVerdicts } from "./judge.js";

describe("judge", () => {
  it("parses the outermost JSON array out of chatty stdout and validates each cell", () => {
    const out = 'thinking...\n[{"story":0,"criterion":1,"pass":true,"reason":"ok"}]\ndone';
    expect(parseVerdicts(out)).toEqual([{ story: 0, criterion: 1, pass: true, reason: "ok" }]);
    expect(() => parseVerdicts("no json here")).toThrow(/array/);
    expect(() => parseVerdicts('[{"story":0,"criterion":9,"pass":true}]')).toThrow(/malformed/);
    expect(() => parseVerdicts('[{"story":"0","criterion":1,"pass":true}]')).toThrow(/malformed/);
  });
  it("runs a command, feeds it the rubric and digest on stdin, and returns its verdicts", async () => {
    const j = cliJudge("echo", "openai", [
      "node",
      "-e",
      "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>{const i=JSON.parse(s);console.log(JSON.stringify([{story:0,criterion:1,pass:i.digest==='<p>x</p>' && i.rubric.length>100 && i.inputsDir==='/inputs',reason:'r'}]))})",
    ]);
    expect(await j.run("<p>x</p>", "/inputs")).toEqual([{ story: 0, criterion: 1, pass: true, reason: "r" }]);
  });
  it("a non-zero exit is an error, not an empty verdict list", async () => {
    await expect(cliJudge("bad", "google", ["node", "-e", "process.exit(3)"]).run("<p/>", "/x")).rejects.toThrow(/exited 3/);
  });
});

# Library gate on the real lockfile (digest/package-lock.json), 2026-09-22

Command: `cd digest && npm run sbom && still_active --sbom=sbom.cdx.json --fail-if-critical --markdown`.
Result: exit 1. 328 assessed: 198 ok, 71 legacy, 55 stale, 4 archived, 1 unassessable
(`@grpc/grpc-js`, an Octokit parse error on its repo URL, not a finding).

The four archived packages are transitive and none is substitutable at this package's level:

| archived | pulled in by | scope |
|---|---|---|
| json-buffer 3.0.1 | eslint > file-entry-cache > flat-cache > keyv | dev |
| stackback 0.0.2 | vitest > why-is-node-running | dev |
| require-from-string 2.0.2 | @anthropic-ai/claude-agent-sdk > @modelcontextprotocol/sdk > ajv | prod |
| source-map-loader 5.0.0 | @temporalio/worker | prod |

The two production ones sit under the two libraries the spec selects (the Agent SDK and the
Temporal worker). Decision owed by Sean: accept as known, or change a spec-level library. The
build proceeds on the default (accept, re-run the gate on every lockfile change).

| activity | up to date? | OpenSSF | vulns | name | version used | latest version | latest pre-release | last commit | libyear | license |
| -------- | ----------- | ------- | ----- | ---- | ------------ | -------------- | ------------------ | ----------- | ------- | ------- |
|  | ✅ | ❓ | ✅ | npm/@anthropic-ai/claude-agent-sdk-darwin-arm64 | 0.3.278 (2026/09) | 0.3.278 (2026/09) | ❓ | ❓ | 0.0y | non-standard |
|  | ✅ | ❓ | ✅ | [npm/@anthropic-ai/claude-agent-sdk](https://github.com/anthropics/claude-agent-sdk-typescript) | 0.3.278 (2026/09) | 0.3.278 (2026/09) | ❓ | [2026/09](https://github.com/anthropics/claude-agent-sdk-typescript) | 0.0y | non-standard |
|  | ✅ | ❓ | ✅ | [npm/@anthropic-ai/sdk](https://github.com/anthropics/anthropic-sdk-typescript) | 0.127.0 (2026/09) | 0.127.0 (2026/09) | ❓ | [2026/09](https://github.com/anthropics/anthropic-sdk-typescript) | 0.0y | MIT |
|  | ⚠️ | 7.2/10 | ✅ | [npm/@babel/runtime](https://github.com/babel/babel) | 7.29.7 (2026/05) | 8.0.5 (2026/09) | ❓ | [2026/09](https://github.com/babel/babel) | 0.3y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@eslint-community/eslint-utils](https://github.com/eslint-community/eslint-utils) | 4.10.1 (2026/07) | 4.10.1 (2026/07) | ❓ | [2026/07](https://github.com/eslint-community/eslint-utils) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@eslint-community/regexpp](https://github.com/eslint-community/regexpp) | 4.12.2 (2025/10) | 4.12.2 (2025/10) | ❓ | [2025/10](https://github.com/eslint-community/regexpp) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@eslint/config-array](https://github.com/eslint/rewrite) | 0.23.5 (2026/04) | 0.23.5 (2026/04) | ❓ | [2026/09](https://github.com/eslint/rewrite) | 0.0y | Apache-2.0 |
|  | ⚠️ | ❓ | ✅ | [npm/@eslint/config-helpers](https://github.com/eslint/rewrite) | 0.5.5 (2026/04) | 0.7.0 (2026/07) | ❓ | [2026/09](https://github.com/eslint/rewrite) | 0.3y | Apache-2.0 |
|  | ✅ | ❓ | ✅ | [npm/@eslint/core](https://github.com/eslint/rewrite) | 1.2.1 (2026/04) | 1.2.1 (2026/04) | ❓ | [2026/09](https://github.com/eslint/rewrite) | 0.0y | Apache-2.0 |
|  | ✅ | ❓ | ✅ | [npm/@eslint/object-schema](https://github.com/eslint/rewrite) | 3.0.5 (2026/04) | 3.0.5 (2026/04) | ❓ | [2026/09](https://github.com/eslint/rewrite) | 0.0y | Apache-2.0 |
|  | ⚠️ | ❓ | ✅ | [npm/@eslint/plugin-kit](https://github.com/eslint/rewrite) | 0.6.1 (2026/03) | 0.7.3 (2026/09) | ❓ | [2026/09](https://github.com/eslint/rewrite) | 0.5y | Apache-2.0 |
|  | ✅ | 6.5/10 | ✅ | [npm/@grpc/proto-loader](https://github.com/grpc/grpc-node) | 0.8.1 (2026/05) | 0.8.1 (2026/05) | ❓ | [2026/09](https://github.com/grpc/grpc-node) | 0.0y | Apache-2.0 |
|  | ✅ | ❓ | ✅ | [npm/@hono/node-server](https://github.com/honojs/node-server) | 2.1.1 (2026/08) | 2.1.1 (2026/08) | ❓ | [2026/08](https://github.com/honojs/node-server) | 0.0y | MIT |
|  | ⚠️ | ❓ | ✅ | [npm/@humanfs/core](https://github.com/humanwhocodes/humanfs) | 0.19.2 (2026/04) | 0.20.0 (2026/09) | ❓ | [2026/09](https://github.com/humanwhocodes/humanfs) | 0.4y | Apache-2.0 |
|  | ⚠️ | ❓ | ✅ | [npm/@humanfs/node](https://github.com/humanwhocodes/humanfs) | 0.16.8 (2026/04) | 0.17.0 (2026/09) | ❓ | [2026/09](https://github.com/humanwhocodes/humanfs) | 0.4y | Apache-2.0 |
|  | ⚠️ | ❓ | ✅ | [npm/@humanfs/types](https://github.com/humanwhocodes/humanfs) | 0.15.0 (2024/09) | 0.16.0 (2026/09) | ❓ | [2026/09](https://github.com/humanwhocodes/humanfs) | 2.0y | Apache-2.0 |
| 🚩 | ✅ | ❓ | ✅ | [npm/@humanwhocodes/module-importer](https://github.com/humanwhocodes/module-importer) | 1.0.1 (2022/08) | 1.0.1 (2022/08) | ❓ | [2025/02](https://github.com/humanwhocodes/module-importer) | 0.0y | Apache-2.0 |
|  | ✅ | ❓ | ✅ | [npm/@humanwhocodes/retry](https://github.com/humanwhocodes/retry) | 0.4.3 (2025/05) | 0.4.3 (2025/05) | ❓ | [2026/07](https://github.com/humanwhocodes/retry) | 0.0y | Apache-2.0 |
|  | ✅ | ❓ | ✅ | [npm/@jridgewell/gen-mapping](https://github.com/jridgewell/sourcemaps) | 0.3.13 (2025/08) | 0.3.13 (2025/08) | ❓ | [2026/08](https://github.com/jridgewell/sourcemaps) | 0.0y | MIT |
| ⚠️ | ✅ | 2.8/10 | ✅ | [npm/@jridgewell/resolve-uri](https://github.com/jridgewell/resolve-uri) | 3.1.2 (2024/02) | 3.1.2 (2024/02) | ❓ | [2026/03](https://github.com/jridgewell/resolve-uri) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@jridgewell/source-map](https://github.com/jridgewell/sourcemaps) | 0.3.11 (2025/08) | 0.3.11 (2025/08) | ❓ | [2026/08](https://github.com/jridgewell/sourcemaps) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@jridgewell/sourcemap-codec](https://github.com/jridgewell/sourcemaps) | 1.6.0 (2026/08) | 1.6.0 (2026/08) | ❓ | [2026/08](https://github.com/jridgewell/sourcemaps) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@jridgewell/trace-mapping](https://github.com/jridgewell/sourcemaps) | 0.3.31 (2025/09) | 0.3.31 (2025/09) | ❓ | [2026/08](https://github.com/jridgewell/sourcemaps) | 0.0y | MIT |
| 🚩 | ✅ | 4/10 | ✅ | [npm/@js-sdsl/ordered-map](https://github.com/js-sdsl/js-sdsl) | 4.4.2 (2023/07) | 4.4.2 (2023/07) | ❓ | [2026/04](https://github.com/js-sdsl/js-sdsl) | 0.0y | MIT |
|  | ⚠️ | ❓ | ✅ | [npm/@jsonjoy.com/base64](https://github.com/jsonjoy-com/base64) | 1.1.2 (2024/05) | 18.30.0 (2026/09) | ❓ | [2024/06](https://github.com/jsonjoy-com/base64) | 2.3y | Apache-2.0 |
|  | ⚠️ | ❓ | ✅ | [npm/@jsonjoy.com/buffers](https://jsonjoy-com/buffers) | 17.67.0 (2026/02) | 18.30.0 (2026/09) | ❓ | ❓ | 0.6y | Apache-2.0 |
|  | ⚠️ | ❓ | ✅ | [npm/@jsonjoy.com/codegen](https://github.com/jsonjoy-com/codegen) | 1.0.0 (2025/08) | 18.30.0 (2026/09) | ❓ | [2025/08](https://github.com/jsonjoy-com/codegen) | 1.1y | Apache-2.0 |
|  | ✅ | 5.5/10 | ✅ | [npm/@jsonjoy.com/fs-core](https://github.com/streamich/memfs) | 4.78.1 (2026/09) | 4.78.1 (2026/09) | ❓ | [2026/09](https://github.com/streamich/memfs) | 0.0y | Apache-2.0 |
|  | ✅ | 5.5/10 | ✅ | [npm/@jsonjoy.com/fs-fsa](https://github.com/streamich/memfs) | 4.78.1 (2026/09) | 4.78.1 (2026/09) | ❓ | [2026/09](https://github.com/streamich/memfs) | 0.0y | Apache-2.0 |
|  | ✅ | 5.5/10 | ✅ | [npm/@jsonjoy.com/fs-node-builtins](https://github.com/streamich/memfs) | 4.78.1 (2026/09) | 4.78.1 (2026/09) | ❓ | [2026/09](https://github.com/streamich/memfs) | 0.0y | Apache-2.0 |
|  | ✅ | 5.5/10 | ✅ | [npm/@jsonjoy.com/fs-node-to-fsa](https://github.com/streamich/memfs) | 4.78.1 (2026/09) | 4.78.1 (2026/09) | ❓ | [2026/09](https://github.com/streamich/memfs) | 0.0y | Apache-2.0 |
|  | ✅ | 5.5/10 | ✅ | [npm/@jsonjoy.com/fs-node-utils](https://github.com/streamich/memfs) | 4.78.1 (2026/09) | 4.78.1 (2026/09) | ❓ | [2026/09](https://github.com/streamich/memfs) | 0.0y | Apache-2.0 |
|  | ✅ | 5.5/10 | ✅ | [npm/@jsonjoy.com/fs-node](https://github.com/streamich/memfs) | 4.78.1 (2026/09) | 4.78.1 (2026/09) | ❓ | [2026/09](https://github.com/streamich/memfs) | 0.0y | Apache-2.0 |
|  | ✅ | 5.5/10 | ✅ | [npm/@jsonjoy.com/fs-print](https://github.com/streamich/memfs) | 4.78.1 (2026/09) | 4.78.1 (2026/09) | ❓ | [2026/09](https://github.com/streamich/memfs) | 0.0y | Apache-2.0 |
|  | ✅ | 5.5/10 | ✅ | [npm/@jsonjoy.com/fs-snapshot](https://github.com/streamich/memfs) | 4.78.1 (2026/09) | 4.78.1 (2026/09) | ❓ | [2026/09](https://github.com/streamich/memfs) | 0.0y | Apache-2.0 |
|  | ⚠️ | ❓ | ✅ | [npm/@jsonjoy.com/json-pack](https://github.com/jsonjoy-com/json-pack) | 1.21.0 (2025/10) | 18.30.0 (2026/09) | ❓ | [2025/10](https://github.com/jsonjoy-com/json-pack) | 0.9y | Apache-2.0 |
|  | ⚠️ | ❓ | ✅ | [npm/@jsonjoy.com/json-pointer](https://github.com/jsonjoy-com/json-pointer) | 1.0.2 (2025/08) | 18.30.0 (2026/09) | ❓ | [2025/08](https://github.com/jsonjoy-com/json-pointer) | 1.0y | Apache-2.0 |
|  | ⚠️ | ❓ | ✅ | [npm/@jsonjoy.com/util](https://github.com/jsonjoy-com/util) | 1.9.0 (2025/08) | 18.30.0 (2026/09) | ❓ | [2025/08](https://github.com/jsonjoy-com/util) | 1.1y | Apache-2.0 |
|  | ✅ | ❓ | ✅ | [npm/@modelcontextprotocol/sdk](https://github.com/modelcontextprotocol/typescript-sdk) | 1.30.0 (2026/07) | 1.30.0 (2026/07) | ❓ | [2026/09](https://github.com/modelcontextprotocol/typescript-sdk) | 0.0y | MIT |
|  | ⚠️ | ❓ | ✅ | [npm/@oxc-project/types](https://github.com/oxc-project/oxc) | 0.150.0 (2026/09) | 0.151.0 (2026/09) | ❓ | [2026/09](https://github.com/oxc-project/oxc) | 0.0y | MIT |
| 🚩 | ✅ | 6.2/10 | ✅ | [npm/@protobufjs/aspromise](https://github.com/dcodeIO/protobuf.js) | 1.1.2 (2017/04) | 1.1.2 (2017/04) | ❓ | [2026/09](https://github.com/dcodeIO/protobuf.js) | 0.0y | BSD-3-Clause |
| 🚩 | ✅ | 6.2/10 | ✅ | [npm/@protobufjs/base64](https://github.com/dcodeIO/protobuf.js) | 1.1.2 (2017/06) | 1.1.2 (2017/06) | ❓ | [2026/09](https://github.com/dcodeIO/protobuf.js) | 0.0y | BSD-3-Clause |
|  | ✅ | 6.2/10 | ✅ | [npm/@protobufjs/codegen](https://github.com/dcodeIO/protobuf.js) | 2.0.5 (2026/04) | 2.0.5 (2026/04) | ❓ | [2026/09](https://github.com/dcodeIO/protobuf.js) | 0.0y | BSD-3-Clause |
|  | ✅ | 6.2/10 | ✅ | [npm/@protobufjs/eventemitter](https://github.com/dcodeIO/protobuf.js) | 1.1.1 (2026/05) | 1.1.1 (2026/05) | ❓ | [2026/09](https://github.com/dcodeIO/protobuf.js) | 0.0y | BSD-3-Clause |
|  | ✅ | 6.2/10 | ✅ | [npm/@protobufjs/fetch](https://github.com/dcodeIO/protobuf.js) | 1.1.1 (2026/05) | 1.1.1 (2026/05) | ❓ | [2026/09](https://github.com/dcodeIO/protobuf.js) | 0.0y | BSD-3-Clause |
| 🚩 | ✅ | 6.2/10 | ✅ | [npm/@protobufjs/float](https://github.com/dcodeIO/protobuf.js) | 1.0.2 (2017/04) | 1.0.2 (2017/04) | ❓ | [2026/09](https://github.com/dcodeIO/protobuf.js) | 0.0y | BSD-3-Clause |
| 🚩 | ✅ | 6.2/10 | ✅ | [npm/@protobufjs/path](https://github.com/dcodeIO/protobuf.js) | 1.1.2 (2017/02) | 1.1.2 (2017/02) | ❓ | [2026/09](https://github.com/dcodeIO/protobuf.js) | 0.0y | BSD-3-Clause |
| 🚩 | ✅ | 6.2/10 | ✅ | [npm/@protobufjs/pool](https://github.com/dcodeIO/protobuf.js) | 1.1.0 (2017/01) | 1.1.0 (2017/01) | ❓ | [2026/09](https://github.com/dcodeIO/protobuf.js) | 0.0y | BSD-3-Clause |
|  | ✅ | 6.2/10 | ✅ | [npm/@protobufjs/utf8](https://github.com/protobufjs/protobuf.js) | 1.1.2 (2026/07) | 1.1.2 (2026/07) | ❓ | [2026/09](https://github.com/protobufjs/protobuf.js) | 0.0y | BSD-3-Clause |
|  | ✅ | ❓ | ✅ | [npm/@rolldown/binding-darwin-arm64](https://github.com/rolldown/rolldown) | 1.2.9 (2026/09) | 1.2.9 (2026/09) | ❓ | [2026/09](https://github.com/rolldown/rolldown) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@rolldown/pluginutils](https://github.com/rolldown/plugins) | 1.0.1 (2026/05) | 1.0.1 (2026/05) | ❓ | [2026/09](https://github.com/rolldown/plugins) | 0.0y | MIT |
| ⚠️ | ⚠️ | 2.9/10 | ✅ | [npm/@stablelib/base64](https://github.com/StableLib/stablelib) | 1.0.1 (2021/05) | 2.0.1 (2025/01) | ❓ | [2026/04](https://github.com/StableLib/stablelib) | 3.6y | MIT |
|  | ✅ | 7.1/10 | ✅ | [npm/@swc/core-darwin-arm64](https://github.com/swc-project/swc) | 1.16.2 (2026/09) | 1.16.2 (2026/09) | ❓ | [2026/09](https://github.com/swc-project/swc) | 0.0y | Apache-2.0 AND MIT |
|  | ✅ | 7.1/10 | ✅ | [npm/@swc/core](https://github.com/swc-project/swc) | 1.16.2 (2026/09) | 1.16.2 (2026/09) | ❓ | [2026/09](https://github.com/swc-project/swc) | 0.0y | Apache-2.0 |
| ⚠️ | ✅ | ❓ | ✅ | [npm/@swc/counter](https://github.com/swc-project/pkgs) | 0.1.3 (2024/02) | 0.1.3 (2024/02) | ❓ | [2026/08](https://github.com/swc-project/pkgs) | 0.0y | Apache-2.0 |
|  | ✅ | 7.1/10 | ✅ | [npm/@swc/types](https://github.com/swc-project/swc) | 0.1.28 (2026/07) | 0.1.28 (2026/07) | ❓ | [2026/09](https://github.com/swc-project/swc) | 0.0y | Apache-2.0 |
|  | ✅ | ❓ | ✅ | [npm/@temporalio/activity](https://github.com/temporalio/sdk-typescript) | 1.24.0 (2026/09) | 1.24.0 (2026/09) | ❓ | [2026/09](https://github.com/temporalio/sdk-typescript) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@temporalio/client](https://github.com/temporalio/sdk-typescript) | 1.24.0 (2026/09) | 1.24.0 (2026/09) | ❓ | [2026/09](https://github.com/temporalio/sdk-typescript) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@temporalio/common](https://github.com/temporalio/sdk-typescript) | 1.24.0 (2026/09) | 1.24.0 (2026/09) | ❓ | [2026/09](https://github.com/temporalio/sdk-typescript) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@temporalio/core-bridge](https://github.com/temporalio/sdk-typescript) | 1.24.0 (2026/09) | 1.24.0 (2026/09) | ❓ | [2026/09](https://github.com/temporalio/sdk-typescript) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@temporalio/nexus](https://github.com/temporalio/sdk-typescript) | 1.24.0 (2026/09) | 1.24.0 (2026/09) | ❓ | [2026/09](https://github.com/temporalio/sdk-typescript) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@temporalio/proto](https://github.com/temporalio/sdk-typescript) | 1.24.0 (2026/09) | 1.24.0 (2026/09) | ❓ | [2026/09](https://github.com/temporalio/sdk-typescript) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@temporalio/testing](https://github.com/temporalio/sdk-typescript) | 1.24.0 (2026/09) | 1.24.0 (2026/09) | ❓ | [2026/09](https://github.com/temporalio/sdk-typescript) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@temporalio/worker](https://github.com/temporalio/sdk-typescript) | 1.24.0 (2026/09) | 1.24.0 (2026/09) | ❓ | [2026/09](https://github.com/temporalio/sdk-typescript) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@temporalio/workflow](https://github.com/temporalio/sdk-typescript) | 1.24.0 (2026/09) | 1.24.0 (2026/09) | ❓ | [2026/09](https://github.com/temporalio/sdk-typescript) | 0.0y | MIT |
|  | ✅ | 6.5/10 | ✅ | [npm/@types/chai](https://github.com/DefinitelyTyped/DefinitelyTyped) | 5.2.3 (2025/10) | 5.2.3 (2025/10) | ❓ | [2026/09](https://github.com/DefinitelyTyped/DefinitelyTyped) | 0.0y | MIT |
| ⚠️ | ✅ | 6.5/10 | ✅ | [npm/@types/deep-eql](https://github.com/DefinitelyTyped/DefinitelyTyped) | 4.0.2 (2023/11) | 4.0.2 (2023/11) | ❓ | [2026/09](https://github.com/DefinitelyTyped/DefinitelyTyped) | 0.0y | MIT |
|  | ✅ | 6.5/10 | ✅ | [npm/@types/esrecurse](https://github.com/DefinitelyTyped/DefinitelyTyped) | 4.3.1 (2025/07) | 4.3.1 (2025/07) | ❓ | [2026/09](https://github.com/DefinitelyTyped/DefinitelyTyped) | 0.0y | MIT |
|  | ✅ | 6.5/10 | ✅ | [npm/@types/estree](https://github.com/DefinitelyTyped/DefinitelyTyped) | 1.0.9 (2026/05) | 1.0.9 (2026/05) | ❓ | [2026/09](https://github.com/DefinitelyTyped/DefinitelyTyped) | 0.0y | MIT |
| ⚠️ | ✅ | 6.5/10 | ✅ | [npm/@types/json-schema](https://github.com/DefinitelyTyped/DefinitelyTyped) | 7.0.15 (2023/11) | 7.0.15 (2023/11) | ❓ | [2026/09](https://github.com/DefinitelyTyped/DefinitelyTyped) | 0.0y | MIT |
|  | ⚠️ | 6.5/10 | ✅ | [npm/@types/node](https://github.com/DefinitelyTyped/DefinitelyTyped) | 24.13.6 (2026/09) | 26.6.2 (2026/09) | ❓ | [2026/09](https://github.com/DefinitelyTyped/DefinitelyTyped) | 0.0y | MIT |
|  | ✅ | 6.7/10 | ✅ | [npm/@typescript-eslint/eslint-plugin](https://github.com/typescript-eslint/typescript-eslint) | 8.70.1 (2026/09) | 8.70.1 (2026/09) | ❓ | [2026/09](https://github.com/typescript-eslint/typescript-eslint) | 0.0y | MIT |
|  | ✅ | 6.7/10 | ✅ | [npm/@typescript-eslint/parser](https://github.com/typescript-eslint/typescript-eslint) | 8.70.1 (2026/09) | 8.70.1 (2026/09) | ❓ | [2026/09](https://github.com/typescript-eslint/typescript-eslint) | 0.0y | MIT |
|  | ✅ | 6.7/10 | ✅ | [npm/@typescript-eslint/project-service](https://github.com/typescript-eslint/typescript-eslint) | 8.70.1 (2026/09) | 8.70.1 (2026/09) | ❓ | [2026/09](https://github.com/typescript-eslint/typescript-eslint) | 0.0y | MIT |
|  | ✅ | 6.7/10 | ✅ | [npm/@typescript-eslint/scope-manager](https://github.com/typescript-eslint/typescript-eslint) | 8.70.1 (2026/09) | 8.70.1 (2026/09) | ❓ | [2026/09](https://github.com/typescript-eslint/typescript-eslint) | 0.0y | MIT |
|  | ✅ | 6.7/10 | ✅ | [npm/@typescript-eslint/tsconfig-utils](https://github.com/typescript-eslint/typescript-eslint) | 8.70.1 (2026/09) | 8.70.1 (2026/09) | ❓ | [2026/09](https://github.com/typescript-eslint/typescript-eslint) | 0.0y | MIT |
|  | ✅ | 6.7/10 | ✅ | [npm/@typescript-eslint/type-utils](https://github.com/typescript-eslint/typescript-eslint) | 8.70.1 (2026/09) | 8.70.1 (2026/09) | ❓ | [2026/09](https://github.com/typescript-eslint/typescript-eslint) | 0.0y | MIT |
|  | ✅ | 6.7/10 | ✅ | [npm/@typescript-eslint/types](https://github.com/typescript-eslint/typescript-eslint) | 8.70.1 (2026/09) | 8.70.1 (2026/09) | ❓ | [2026/09](https://github.com/typescript-eslint/typescript-eslint) | 0.0y | MIT |
|  | ✅ | 6.7/10 | ✅ | [npm/@typescript-eslint/typescript-estree](https://github.com/typescript-eslint/typescript-eslint) | 8.70.1 (2026/09) | 8.70.1 (2026/09) | ❓ | [2026/09](https://github.com/typescript-eslint/typescript-eslint) | 0.0y | MIT |
|  | ✅ | 6.7/10 | ✅ | [npm/@typescript-eslint/utils](https://github.com/typescript-eslint/typescript-eslint) | 8.70.1 (2026/09) | 8.70.1 (2026/09) | ❓ | [2026/09](https://github.com/typescript-eslint/typescript-eslint) | 0.0y | MIT |
|  | ✅ | 6.7/10 | ✅ | [npm/@typescript-eslint/visitor-keys](https://github.com/typescript-eslint/typescript-eslint) | 8.70.1 (2026/09) | 8.70.1 (2026/09) | ❓ | [2026/09](https://github.com/typescript-eslint/typescript-eslint) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@vitest/mocker](https://github.com/vitest-dev/vitest) | 5.0.1 (2026/09) | 5.0.1 (2026/09) | ❓ | [2026/09](https://github.com/vitest-dev/vitest) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/@vitest/spy](https://github.com/vitest-dev/vitest) | 5.0.1 (2026/09) | 5.0.1 (2026/09) | ❓ | [2026/09](https://github.com/vitest-dev/vitest) | 0.0y | MIT |
| ⚠️ | ✅ | 1.7/10 | ✅ | [npm/@webassemblyjs/ast](https://github.com/xtuc/webassemblyjs) | 1.14.1 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ⚠️ | 1.7/10 | ✅ | [npm/@webassemblyjs/floating-point-hex-parser](https://github.com/xtuc/webassemblyjs) | 1.13.2 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ⚠️ | 1.7/10 | ✅ | [npm/@webassemblyjs/helper-api-error](https://github.com/xtuc/webassemblyjs) | 1.13.2 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ✅ | 1.7/10 | ✅ | [npm/@webassemblyjs/helper-buffer](https://github.com/xtuc/webassemblyjs) | 1.14.1 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ⚠️ | 1.7/10 | ✅ | [npm/@webassemblyjs/helper-numbers](https://github.com/xtuc/webassemblyjs) | 1.13.2 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ⚠️ | 1.7/10 | ✅ | [npm/@webassemblyjs/helper-wasm-bytecode](https://github.com/xtuc/webassemblyjs) | 1.13.2 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ✅ | 1.7/10 | ✅ | [npm/@webassemblyjs/helper-wasm-section](https://github.com/xtuc/webassemblyjs) | 1.14.1 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ⚠️ | 1.7/10 | ✅ | [npm/@webassemblyjs/ieee754](https://github.com/xtuc/webassemblyjs) | 1.13.2 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ⚠️ | 1.7/10 | ✅ | [npm/@webassemblyjs/leb128](https://github.com/xtuc/webassemblyjs) | 1.13.2 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | Apache-2.0 |
| ⚠️ | ⚠️ | 1.7/10 | ✅ | [npm/@webassemblyjs/utf8](https://github.com/xtuc/webassemblyjs) | 1.13.2 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ✅ | 1.7/10 | ✅ | [npm/@webassemblyjs/wasm-edit](https://github.com/xtuc/webassemblyjs) | 1.14.1 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ✅ | 1.7/10 | ✅ | [npm/@webassemblyjs/wasm-gen](https://github.com/xtuc/webassemblyjs) | 1.14.1 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ✅ | 1.7/10 | ✅ | [npm/@webassemblyjs/wasm-opt](https://github.com/xtuc/webassemblyjs) | 1.14.1 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ✅ | 1.7/10 | ✅ | [npm/@webassemblyjs/wasm-parser](https://github.com/xtuc/webassemblyjs) | 1.14.1 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| ⚠️ | ✅ | 1.7/10 | ✅ | [npm/@webassemblyjs/wast-printer](https://github.com/xtuc/webassemblyjs) | 1.14.1 (2024/11) | 1.14.1 (2024/11) | ❓ | [2025/01](https://github.com/xtuc/webassemblyjs) | 0.0y | MIT |
| 🚩 | ✅ | 2.4/10 | ✅ | [npm/@xtuc/ieee754](https://github.com/feross/ieee754) | 1.2.0 (2018/07) | 1.2.0 (2018/07) | ❓ | [2021/08](https://github.com/feross/ieee754) | 0.0y | BSD-3-Clause |
| 🚩 | ✅ | 3.1/10 | ✅ | [npm/@xtuc/long](https://github.com/dcodeIO/long.js) | 4.2.2 (2019/02) | 4.2.2 (2019/02) | ❓ | [2026/09](https://github.com/dcodeIO/long.js) | 0.0y | Apache-2.0 |
| 🚩 | ✅ | 2.1/10 | ✅ | [npm/abort-controller](https://github.com/mysticatea/abort-controller) | 3.0.0 (2019/03) | 3.0.0 (2019/03) | ❓ | [2021/03](https://github.com/mysticatea/abort-controller) | 0.0y | MIT |
| ⚠️ | ✅ | 5.9/10 | ✅ | [npm/accepts](https://github.com/jshttp/accepts) | 2.0.0 (2024/08) | 2.0.0 (2024/08) | ❓ | [2026/04](https://github.com/jshttp/accepts) | 0.0y | MIT |
| 🚩 | ✅ | 2.6/10 | ✅ | [npm/acorn-jsx](https://github.com/acornjs/acorn-jsx) | 5.3.2 (2021/07) | 5.3.2 (2021/07) | ❓ | [2022/12](https://github.com/acornjs/acorn-jsx) | 0.0y | MIT |
|  | ✅ | 4.9/10 | ✅ | [npm/acorn](https://github.com/acornjs/acorn) | 8.18.0 (2026/07) | 8.18.0 (2026/07) | ❓ | [2026/09](https://github.com/acornjs/acorn) | 0.0y | MIT |
| ⚠️ | ✅ | 3.5/10 | ✅ | [npm/ajv-formats](https://github.com/ajv-validator/ajv-formats) | 3.0.1 (2024/03) | 3.0.1 (2024/03) | ❓ | [2024/08](https://github.com/ajv-validator/ajv-formats) | 0.0y | MIT |
| 🚩 | ✅ | 3.2/10 | ✅ | [npm/ajv-keywords](https://github.com/epoberezkin/ajv-keywords) | 5.1.0 (2021/11) | 5.1.0 (2021/11) | ❓ | [2023/04](https://github.com/epoberezkin/ajv-keywords) | 0.0y | MIT |
|  | ✅ | 4.8/10 | ✅ | [npm/ajv](https://github.com/ajv-validator/ajv) | 8.20.0 (2026/04) | 8.20.0 (2026/04) | ❓ | [2026/09](https://github.com/ajv-validator/ajv) | 0.0y | MIT |
|  | ⚠️ | 3.8/10 | ✅ | [npm/ansi-regex](https://github.com/chalk/ansi-regex) | 5.0.1 (2021/09) | 6.3.0 (2026/08) | ❓ | [2026/09](https://github.com/chalk/ansi-regex) | 4.9y | MIT |
|  | ⚠️ | 4/10 | ✅ | [npm/ansi-styles](https://github.com/chalk/ansi-styles) | 4.3.0 (2020/10) | 7.0.0 (2026/07) | ❓ | [2026/09](https://github.com/chalk/ansi-styles) | 5.8y | MIT |
| ⚠️ | ✅ | 4.7/10 | ✅ | [npm/assertion-error](https://github.com/chaijs/assertion-error) | 2.0.1 (2023/10) | 2.0.1 (2023/10) | ❓ | [2026/05](https://github.com/chaijs/assertion-error) | 0.0y | MIT |
|  | ✅ | 5.6/10 | ✅ | [npm/balanced-match](https://github.com/juliangruber/balanced-match) | 4.0.4 (2026/02) | 4.0.4 (2026/02) | ❓ | [2026/08](https://github.com/juliangruber/balanced-match) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/baseline-browser-mapping](https://github.com/web-platform-dx/baseline-browser-mapping) | 2.11.25 (2026/09) | 2.11.25 (2026/09) | ❓ | [2026/09](https://github.com/web-platform-dx/baseline-browser-mapping) | 0.0y | Apache-2.0 |
|  | ✅ | 7.4/10 | ✅ | [npm/body-parser](https://github.com/expressjs/body-parser) | 2.3.0 (2026/06) | 2.3.0 (2026/06) | ❓ | [2026/09](https://github.com/expressjs/body-parser) | 0.0y | MIT |
|  | ✅ | 7.2/10 | ✅ | [npm/brace-expansion](https://github.com/juliangruber/brace-expansion) | 5.0.12 (2026/09) | 5.0.12 (2026/09) | ❓ | [2026/09](https://github.com/juliangruber/brace-expansion) | 0.0y | MIT |
|  | ✅ | 6.5/10 | ✅ | [npm/browserslist](https://github.com/browserslist/browserslist) | 4.29.0 (2026/09) | 4.29.0 (2026/09) | ❓ | [2026/09](https://github.com/browserslist/browserslist) | 0.0y | MIT |
| 🚩 | ✅ | 2/10 | ✅ | [npm/buffer-from](https://github.com/LinusU/buffer-from) | 1.1.2 (2021/07) | 1.1.2 (2021/07) | ❓ | [2021/07](https://github.com/LinusU/buffer-from) | 0.0y | MIT |
| 🚩 | ✅ | 2.8/10 | ✅ | [npm/bytes](https://github.com/visionmedia/bytes.js) | 3.1.2 (2022/01) | 3.1.2 (2022/01) | ❓ | [2024/07](https://github.com/visionmedia/bytes.js) | 0.0y | MIT |
| ⚠️ | ✅ | ❓ | ✅ | [npm/call-bind-apply-helpers](https://github.com/ljharb/call-bind-apply-helpers) | 1.0.2 (2025/02) | 1.0.2 (2025/02) | ❓ | [2025/02](https://github.com/ljharb/call-bind-apply-helpers) | 0.0y | MIT |
| ⚠️ | ✅ | ❓ | ✅ | [npm/call-bound](https://github.com/ljharb/call-bound) | 1.0.4 (2025/03) | 1.0.4 (2025/03) | ❓ | [2025/03](https://github.com/ljharb/call-bound) | 0.0y | MIT |
|  | ✅ | 4.6/10 | ✅ | [npm/caniuse-lite](https://github.com/browserslist/caniuse-lite) | 1.0.30001810 (2026/08) | 1.0.30001810 (2026/08) | ❓ | [2026/08](https://github.com/browserslist/caniuse-lite) | 0.0y | CC-BY-4.0 |
|  | ✅ | 6.9/10 | ✅ | [npm/chai](https://github.com/chaijs/chai) | 6.2.2 (2025/12) | 6.2.2 (2025/12) | ❓ | [2026/09](https://github.com/chaijs/chai) | 0.0y | MIT |
| ⚠️ | ✅ | 2.2/10 | ✅ | [npm/chrome-trace-event](https://github.com/samccone/chrome-trace-event) | 1.0.4 (2024/05) | 1.0.4 (2024/05) | ❓ | [2024/05](https://github.com/samccone/chrome-trace-event) | 0.0y | MIT |
| ⚠️ | ⚠️ | 4.4/10 | ✅ | [npm/cliui](https://github.com/yargs/cliui) | 8.0.1 (2022/10) | 9.0.1 (2025/03) | ❓ | [2026/09](https://github.com/yargs/cliui) | 2.5y | ISC |
|  | ⚠️ | 3.3/10 | ✅ | [npm/color-convert](https://github.com/Qix-/color-convert) | 2.0.1 (2019/08) | 3.1.3 (2025/11) | ❓ | [2025/11](https://github.com/Qix-/color-convert) | 6.2y | MIT |
|  | ⚠️ | 3/10 | ✅ | [npm/color-name](https://github.com/colorjs/color-name) | 1.1.4 (2018/09) | 2.1.1 (2026/07) | ❓ | [2026/07](https://github.com/colorjs/color-name) | 7.8y | MIT |
|  | ⚠️ | 7.5/10 | ✅ | [npm/commander](https://github.com/tj/commander.js) | 2.20.3 (2019/10) | 15.0.0 (2026/05) | ❓ | [2026/09](https://github.com/tj/commander.js) | 6.6y | MIT |
|  | ⚠️ | 6.8/10 | ✅ | [npm/content-disposition](https://github.com/jshttp/content-disposition) | 1.1.0 (2026/04) | 3.0.0 (2026/08) | ❓ | [2026/09](https://github.com/jshttp/content-disposition) | 0.4y | MIT |
|  | ⚠️ | 6.9/10 | ✅ | [npm/content-type](https://github.com/jshttp/content-type) | 1.0.5 (2023/01) | 3.1.1 (2026/09) | ❓ | [2026/09](https://github.com/jshttp/content-type) | 3.6y | MIT |
| ⚠️ | ✅ | 2.3/10 | ✅ | [npm/cookie-signature](https://github.com/visionmedia/node-cookie-signature) | 1.2.2 (2024/10) | 1.2.2 (2024/10) | ❓ | [2025/04](https://github.com/visionmedia/node-cookie-signature) | 0.0y | MIT |
|  | ⚠️ | 8.2/10 | ✅ | [npm/cookie](https://github.com/jshttp/cookie) | 0.7.2 (2024/10) | 2.0.1 (2026/06) | ❓ | [2026/07](https://github.com/jshttp/cookie) | 1.7y | MIT |
|  | ✅ | 6.4/10 | ✅ | [npm/cors](https://github.com/expressjs/cors) | 2.8.6 (2026/01) | 2.8.6 (2026/01) | ❓ | [2026/06](https://github.com/expressjs/cors) | 0.0y | MIT |
| ⚠️ | ✅ | 3.2/10 | ✅ | [npm/cross-spawn](https://github.com/moxystudio/node-cross-spawn) | 7.0.6 (2024/11) | 7.0.6 (2024/11) | ❓ | [2024/11](https://github.com/moxystudio/node-cross-spawn) | 0.0y | MIT |
|  | ✅ | 2.6/10 | ✅ | [npm/debug](https://github.com/debug-js/debug) | 4.4.3 (2025/09) | 4.4.3 (2025/09) | ❓ | [2026/04](https://github.com/debug-js/debug) | 0.0y | MIT |
| 🚩 | ✅ | 1.9/10 | ✅ | [npm/deep-is](https://github.com/thlorenz/deep-is) | 0.1.4 (2021/09) | 0.1.4 (2021/09) | ❓ | [2021/09](https://github.com/thlorenz/deep-is) | 0.0y | MIT |
| 🚩 | ✅ | 2.8/10 | ✅ | [npm/depd](https://github.com/dougwilson/nodejs-depd) | 2.0.0 (2018/10) | 2.0.0 (2018/10) | ❓ | [2024/09](https://github.com/dougwilson/nodejs-depd) | 0.0y | MIT |
|  | ✅ | 3.8/10 | ✅ | [npm/detect-libc](https://github.com/lovell/detect-libc) | 2.1.2 (2025/10) | 2.1.2 (2025/10) | ❓ | [2025/10](https://github.com/lovell/detect-libc) | 0.0y | Apache-2.0 |
| ⚠️ | ✅ | ❓ | ✅ | [npm/dunder-proto](https://github.com/es-shims/dunder-proto) | 1.0.1 (2024/12) | 1.0.1 (2024/12) | ❓ | [2024/12](https://github.com/es-shims/dunder-proto) | 0.0y | MIT |
| 🚩 | ✅ | 2/10 | ✅ | [npm/ee-first](https://github.com/jonathanong/ee-first) | 1.1.1 (2015/05) | 1.1.1 (2015/05) | ❓ | [2019/04](https://github.com/jonathanong/ee-first) | 0.0y | MIT |
|  | ✅ | 4.5/10 | ✅ | [npm/electron-to-chromium](https://github.com/Kilian/electron-to-chromium) | 1.5.433 (2026/09) | 1.5.433 (2026/09) | ❓ | [2026/09](https://github.com/Kilian/electron-to-chromium) | 0.0y | ISC |
|  | ⚠️ | 3.3/10 | ✅ | [npm/emoji-regex](https://github.com/mathiasbynens/emoji-regex) | 8.0.0 (2019/03) | 11.0.0 (2026/09) | ❓ | [2026/09](https://github.com/mathiasbynens/emoji-regex) | 7.5y | MIT |
| ⚠️ | ✅ | 5.4/10 | ✅ | [npm/encodeurl](https://github.com/pillarjs/encodeurl) | 2.0.0 (2024/03) | 2.0.0 (2024/03) | ❓ | [2026/09](https://github.com/pillarjs/encodeurl) | 0.0y | MIT |
|  | ✅ | 5.7/10 | ✅ | [npm/enhanced-resolve](https://github.com/webpack/enhanced-resolve) | 5.25.1 (2026/09) | 5.25.1 (2026/09) | ❓ | [2026/09](https://github.com/webpack/enhanced-resolve) | 0.0y | MIT |
| ⚠️ | ✅ | ❓ | ✅ | [npm/es-define-property](https://github.com/ljharb/es-define-property) | 1.0.1 (2024/12) | 1.0.1 (2024/12) | ❓ | [2024/12](https://github.com/ljharb/es-define-property) | 0.0y | MIT |
| ⚠️ | ✅ | ❓ | ✅ | [npm/es-errors](https://github.com/ljharb/es-errors) | 1.3.0 (2024/02) | 1.3.0 (2024/02) | ❓ | [2024/03](https://github.com/ljharb/es-errors) | 0.0y | MIT |
|  | ⚠️ | 4.7/10 | ✅ | [npm/es-module-lexer](https://github.com/guybedford/es-module-lexer) | 2.3.2 (2026/08) | 3.0.2 (2026/09) | ❓ | [2026/09](https://github.com/guybedford/es-module-lexer) | 0.1y | MIT |
|  | ✅ | ❓ | ✅ | [npm/es-object-atoms](https://github.com/ljharb/es-object-atoms) | 1.1.2 (2026/05) | 1.1.2 (2026/05) | ❓ | [2026/06](https://github.com/ljharb/es-object-atoms) | 0.0y | MIT |
| ⚠️ | ✅ | 2.9/10 | ✅ | [npm/escalade](https://github.com/lukeed/escalade) | 3.2.0 (2024/08) | 3.2.0 (2024/08) | ❓ | [2024/08](https://github.com/lukeed/escalade) | 0.0y | MIT |
| 🚩 | ✅ | 2/10 | ✅ | [npm/escape-html](https://github.com/component/escape-html) | 1.0.3 (2015/09) | 1.0.3 (2015/09) | ❓ | [2022/09](https://github.com/component/escape-html) | 0.0y | MIT |
| 🚩 | ⚠️ | 3.7/10 | ✅ | [npm/escape-string-regexp](https://github.com/sindresorhus/escape-string-regexp) | 4.0.0 (2020/04) | 5.0.0 (2021/04) | ❓ | [2026/09](https://github.com/sindresorhus/escape-string-regexp) | 1.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/eslint-scope](https://github.com/eslint/js) | 9.1.2 (2026/03) | 9.1.2 (2026/03) | ❓ | [2026/09](https://github.com/eslint/js) | 0.0y | BSD-2-Clause |
|  | ✅ | ❓ | ✅ | [npm/eslint-visitor-keys](https://github.com/eslint/js) | 5.0.1 (2026/02) | 5.0.1 (2026/02) | ❓ | [2026/09](https://github.com/eslint/js) | 0.0y | Apache-2.0 |
|  | ⚠️ | 6.3/10 | ✅ | [npm/eslint](https://github.com/eslint/eslint) | 10.0.0 (2026/02) | 10.11.0 (2026/09) | ❓ | [2026/09](https://github.com/eslint/eslint) | 0.6y | MIT |
|  | ✅ | ❓ | ✅ | [npm/espree](https://github.com/eslint/js) | 11.2.0 (2026/03) | 11.2.0 (2026/03) | ❓ | [2026/09](https://github.com/eslint/js) | 0.0y | BSD-2-Clause |
|  | ✅ | 3.6/10 | ✅ | [npm/esquery](https://github.com/estools/esquery) | 1.7.0 (2025/12) | 1.7.0 (2025/12) | ❓ | [2025/12](https://github.com/estools/esquery) | 0.0y | BSD-3-Clause |
| 🚩 | ✅ | 2.2/10 | ✅ | [npm/esrecurse](https://github.com/estools/esrecurse) | 4.3.0 (2020/08) | 4.3.0 (2020/08) | ❓ | [2023/03](https://github.com/estools/esrecurse) | 0.0y | BSD-2-Clause |
| 🚩 | ✅ | 2.6/10 | ✅ | [npm/estraverse](https://github.com/estools/estraverse) | 5.3.0 (2021/10) | 5.3.0 (2021/10) | ❓ | [2022/04](https://github.com/estools/estraverse) | 0.0y | BSD-2-Clause |
| 🚩 | ✅ | 2.1/10 | ✅ | [npm/estree-walker](https://github.com/Rich-Harris/estree-walker) | 3.0.3 (2023/01) | 3.0.3 (2023/01) | ❓ | [2025/09](https://github.com/Rich-Harris/estree-walker) | 0.0y | MIT |
| 🚩 | ✅ | 2.3/10 | ✅ | [npm/esutils](https://github.com/estools/esutils) | 2.0.3 (2019/07) | 2.0.3 (2019/07) | ❓ | [2022/04](https://github.com/estools/esutils) | 0.0y | BSD-2-Clause |
| 🚩 | ✅ | 5.1/10 | ✅ | [npm/etag](https://github.com/jshttp/etag) | 1.8.1 (2017/09) | 1.8.1 (2017/09) | ❓ | [2026/03](https://github.com/jshttp/etag) | 0.0y | MIT |
| 🚩 | ⚠️ | 3/10 | ✅ | [npm/event-target-shim](https://github.com/mysticatea/event-target-shim) | 5.0.1 (2019/02) | 6.0.2 (2021/01) | ❓ | [2023/01](https://github.com/mysticatea/event-target-shim) | 1.9y | MIT |
| 🚩 | ✅ | 3.5/10 | ✅ | [npm/events](https://github.com/Gozala/events) | 3.3.0 (2021/02) | 3.3.0 (2021/02) | ❓ | [2024/12](https://github.com/Gozala/events) | 0.0y | MIT |
|  | ⚠️ | ❓ | ✅ | [npm/eventsource-parser](https://github.com/rexxars/eventsource-parser) | 3.1.1 (2026/08) | 4.1.1 (2026/09) | ❓ | [2026/09](https://github.com/rexxars/eventsource-parser) | 0.1y | MIT |
|  | ⚠️ | 4.4/10 | ✅ | [npm/eventsource](https://github.com/EventSource/eventsource) | 3.0.7 (2025/05) | 5.1.2 (2026/09) | ❓ | [2026/09](https://github.com/EventSource/eventsource) | 1.4y | MIT |
|  | ✅ | 5.1/10 | ✅ | [npm/expect-type](https://github.com/mmkal/expect-type) | 1.4.0 (2026/06) | 1.4.0 (2026/06) | ❓ | [2026/09](https://github.com/mmkal/expect-type) | 0.0y | Apache-2.0 |
|  | ✅ | ❓ | ✅ | [npm/express-rate-limit](https://github.com/express-rate-limit/express-rate-limit) | 8.7.0 (2026/08) | 8.7.0 (2026/08) | ❓ | [2026/09](https://github.com/express-rate-limit/express-rate-limit) | 0.0y | MIT |
|  | ✅ | 8.5/10 | ✅ | [npm/express](https://github.com/expressjs/express) | 5.2.1 (2025/12) | 5.2.1 (2025/12) | ❓ | [2026/09](https://github.com/expressjs/express) | 0.0y | MIT |
| 🚩 | ✅ | 2.3/10 | ✅ | [npm/fast-deep-equal](https://github.com/epoberezkin/fast-deep-equal) | 3.1.3 (2020/06) | 3.1.3 (2020/06) | ❓ | [2023/10](https://github.com/epoberezkin/fast-deep-equal) | 0.0y | MIT |
| 🚩 | ✅ | 2.2/10 | ✅ | [npm/fast-json-stable-stringify](https://github.com/epoberezkin/fast-json-stable-stringify) | 2.1.0 (2019/12) | 2.1.0 (2019/12) | ❓ | [2023/07](https://github.com/epoberezkin/fast-json-stable-stringify) | 0.0y | MIT |
| 🚩 | ⚠️ | 2.1/10 | ✅ | [npm/fast-levenshtein](https://github.com/hiddentao/fast-levenshtein) | 2.0.6 (2016/12) | 3.0.0 (2020/07) | ❓ | [2021/10](https://github.com/hiddentao/fast-levenshtein) | 3.6y | MIT |
| 🚩 | ✅ | 2.1/10 | ✅ | [npm/fast-sha256](https://github.com/dchest/fast-sha256-js) | 1.3.0 (2020/01) | 1.3.0 (2020/01) | ❓ | [2023/07](https://github.com/dchest/fast-sha256-js) | 0.0y | Unlicense |
|  | ⚠️ | ❓ | ✅ | [npm/fast-uri](https://github.com/fastify/fast-uri) | 3.1.8 (2026/09) | 4.2.1 (2026/09) | ❓ | [2026/09](https://github.com/fastify/fast-uri) | 0.0y | BSD-3-Clause |
|  | ✅ | 3.3/10 | ✅ | [npm/fdir](https://github.com/thecodrr/fdir) | 6.5.0 (2025/08) | 6.5.0 (2025/08) | ❓ | [2025/08](https://github.com/thecodrr/fdir) | 0.0y | MIT |
|  | ⚠️ | ❓ | ✅ | [npm/file-entry-cache](https://github.com/jaredwray/file-entry-cache) | 8.0.0 (2023/12) | 11.1.5 (2026/06) | ❓ | ❓ | 2.5y | MIT |
|  | ✅ | 6.8/10 | ✅ | [npm/finalhandler](https://github.com/pillarjs/finalhandler) | 2.1.1 (2025/12) | 2.1.1 (2025/12) | ❓ | [2026/09](https://github.com/pillarjs/finalhandler) | 0.0y | MIT |
|  | ⚠️ | 3.7/10 | ✅ | [npm/find-up](https://github.com/sindresorhus/find-up) | 5.0.0 (2020/08) | 8.0.0 (2025/09) | ❓ | [2026/09](https://github.com/sindresorhus/find-up) | 5.1y | MIT |
|  | ⚠️ | ❓ | ✅ | [npm/flat-cache](https://github.com/jaredwray/flat-cache) | 4.0.1 (2024/03) | 6.1.23 (2026/06) | ❓ | ❓ | 2.3y | MIT |
|  | ✅ | 4.3/10 | ✅ | [npm/flatted](https://github.com/WebReflection/flatted) | 3.4.4 (2026/07) | 3.4.4 (2026/07) | ❓ | [2026/07](https://github.com/WebReflection/flatted) | 0.0y | ISC |
| 🚩 | ✅ | 5.1/10 | ✅ | [npm/forwarded](https://github.com/jshttp/forwarded) | 0.2.0 (2021/05) | 0.2.0 (2021/05) | ❓ | [2026/06](https://github.com/jshttp/forwarded) | 0.0y | MIT |
| ⚠️ | ✅ | 5.5/10 | ✅ | [npm/fresh](https://github.com/jshttp/fresh) | 2.0.0 (2024/09) | 2.0.0 (2024/09) | ❓ | [2026/06](https://github.com/jshttp/fresh) | 0.0y | MIT |
|  | ✅ | 4.2/10 | ✅ | [npm/fs-monkey](https://github.com/streamich/fs-monkey) | 1.1.0 (2025/07) | 1.1.0 (2025/07) | ❓ | [2025/09](https://github.com/streamich/fs-monkey) | 0.0y | Unlicense |
| 🚩 | ✅ | 3.1/10 | ✅ | [npm/fsevents](https://github.com/fsevents/fsevents) | 2.3.3 (2023/08) | 2.3.3 (2023/08) | ❓ | [2024/09](https://github.com/fsevents/fsevents) | 0.0y | MIT |
| ⚠️ | ✅ | 4.5/10 | ✅ | [npm/function-bind](https://github.com/Raynos/function-bind) | 1.1.2 (2023/10) | 1.1.2 (2023/10) | ❓ | [2023/10](https://github.com/Raynos/function-bind) | 0.0y | MIT |
| 🚩 | ✅ | 2.1/10 | ✅ | [npm/get-caller-file](https://github.com/stefanpenner/get-caller-file) | 2.0.5 (2019/03) | 2.0.5 (2019/03) | ❓ | [2023/09](https://github.com/stefanpenner/get-caller-file) | 0.0y | ISC |
|  | ⚠️ | 3.8/10 | ✅ | [npm/get-intrinsic](https://github.com/ljharb/get-intrinsic) | 1.3.0 (2025/02) | 1.3.1 (2025/09) | ❓ | [2026/01](https://github.com/ljharb/get-intrinsic) | 0.6y | MIT |
| ⚠️ | ✅ | ❓ | ✅ | [npm/get-proto](https://github.com/ljharb/get-proto) | 1.0.1 (2025/01) | 1.0.1 (2025/01) | ❓ | [2026/01](https://github.com/ljharb/get-proto) | 0.0y | MIT |
| 🚩 | ✅ | 3.7/10 | ✅ | [npm/glob-parent](https://github.com/gulpjs/glob-parent) | 6.0.2 (2021/09) | 6.0.2 (2021/09) | ❓ | [2024/07](https://github.com/gulpjs/glob-parent) | 0.0y | ISC |
|  | ✅ | ❓ | ✅ | [npm/glob-to-regex.js](https://github.com/streamich/glob-to-regex) | 1.3.1 (2026/09) | 1.3.1 (2026/09) | ❓ | [2026/09](https://github.com/streamich/glob-to-regex) | 0.0y | Apache-2.0 |
| ⚠️ | ✅ | 3.8/10 | ✅ | [npm/gopd](https://github.com/ljharb/gopd) | 1.2.0 (2024/12) | 1.2.0 (2024/12) | ❓ | [2026/04](https://github.com/ljharb/gopd) | 0.0y | MIT |
| 🚩 | ✅ | 3.4/10 | ✅ | [npm/graceful-fs](https://github.com/isaacs/node-graceful-fs) | 4.2.11 (2023/03) | 4.2.11 (2023/03) | ❓ | [2025/10](https://github.com/isaacs/node-graceful-fs) | 0.0y | ISC |
| 🚩 | ⚠️ | 3.6/10 | ✅ | [npm/has-flag](https://github.com/sindresorhus/has-flag) | 4.0.0 (2019/04) | 5.0.1 (2021/07) | ❓ | [2026/09](https://github.com/sindresorhus/has-flag) | 2.3y | MIT |
| ⚠️ | ✅ | 3.8/10 | ✅ | [npm/has-symbols](https://github.com/inspect-js/has-symbols) | 1.1.0 (2024/12) | 1.1.0 (2024/12) | ❓ | [2026/04](https://github.com/inspect-js/has-symbols) | 0.0y | MIT |
|  | ✅ | 4/10 | ✅ | [npm/hasown](https://github.com/inspect-js/hasOwn) | 2.0.4 (2026/05) | 2.0.4 (2026/05) | ❓ | [2026/09](https://github.com/inspect-js/hasOwn) | 0.0y | MIT |
|  | ✅ | 3.3/10 | ✅ | [npm/heap-js](https://github.com/ignlg/heap-js) | 2.7.1 (2025/09) | 2.7.1 (2025/09) | ❓ | [2026/01](https://github.com/ignlg/heap-js) | 0.0y | BSD-3-Clause |
|  | ✅ | ❓ | ✅ | [npm/hono](https://github.com/honojs/hono) | 4.13.8 (2026/09) | 4.13.8 (2026/09) | ❓ | [2026/09](https://github.com/honojs/hono) | 0.0y | MIT |
|  | ✅ | 6.8/10 | ✅ | [npm/http-errors](https://github.com/jshttp/http-errors) | 2.0.1 (2025/11) | 2.0.1 (2025/11) | ❓ | [2026/06](https://github.com/jshttp/http-errors) | 0.0y | MIT |
| 🚩 | ✅ | 2/10 | ✅ | [npm/hyperdyperid](https://github.com/streamich/hyperdyperid) | 1.2.0 (2022/04) | 1.2.0 (2022/04) | ❓ | [2023/12](https://github.com/streamich/hyperdyperid) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/iconv-lite](https://github.com/pillarjs/iconv-lite) | 0.7.3 (2026/07) | 0.7.3 (2026/07) | ❓ | [2026/09](https://github.com/pillarjs/iconv-lite) | 0.0y | MIT |
|  | ⚠️ | 3.3/10 | ✅ | [npm/ignore](https://github.com/kaelzhang/node-ignore) | 5.3.2 (2024/08) | 7.0.9 (2026/09) | ❓ | [2026/09](https://github.com/kaelzhang/node-ignore) | 2.1y | MIT |
| 🚩 | ✅ | 2/10 | ✅ | [npm/imurmurhash](https://github.com/jensyt/imurmurhash-js) | 0.1.4 (2013/08) | 0.1.4 (2013/08) | ❓ | [2013/08](https://github.com/jensyt/imurmurhash-js) | 0.0y | MIT |
| 🚩 | ✅ | 3.6/10 | ✅ | [npm/inherits](https://github.com/isaacs/inherits) | 2.0.4 (2019/06) | 2.0.4 (2019/06) | ❓ | [2025/10](https://github.com/isaacs/inherits) | 0.0y | ISC |
|  | ✅ | 6.9/10 | ✅ | [npm/ip-address](https://github.com/beaugunderson/ip-address) | 10.7.2 (2026/09) | 10.7.2 (2026/09) | ❓ | [2026/09](https://github.com/beaugunderson/ip-address) | 0.0y | MIT |
|  | ⚠️ | 5.3/10 | ✅ | [npm/ipaddr.js](https://github.com/whitequark/ipaddr.js) | 1.9.1 (2019/07) | 2.5.0 (2026/08) | ❓ | [2026/09](https://github.com/whitequark/ipaddr.js) | 7.1y | MIT |
| 🚩 | ✅ | 3/10 | ✅ | [npm/is-extglob](https://github.com/jonschlinkert/is-extglob) | 2.1.1 (2016/12) | 2.1.1 (2016/12) | ❓ | [2019/05](https://github.com/jonschlinkert/is-extglob) | 0.0y | MIT |
|  | ⚠️ | 3.8/10 | ✅ | [npm/is-fullwidth-code-point](https://github.com/sindresorhus/is-fullwidth-code-point) | 3.0.0 (2019/03) | 5.1.0 (2025/08) | ❓ | [2026/09](https://github.com/sindresorhus/is-fullwidth-code-point) | 6.5y | MIT |
| 🚩 | ✅ | 3.6/10 | ✅ | [npm/is-glob](https://github.com/micromatch/is-glob) | 4.0.3 (2021/09) | 4.0.3 (2021/09) | ❓ | [2022/12](https://github.com/micromatch/is-glob) | 0.0y | MIT |
| 🚩 | ✅ | 4.1/10 | ✅ | [npm/is-promise](https://github.com/then/is-promise) | 4.0.0 (2020/04) | 4.0.0 (2020/04) | ❓ | [2023/04](https://github.com/then/is-promise) | 0.0y | MIT |
|  | ⚠️ | 4.5/10 | ✅ | [npm/isexe](https://github.com/isaacs/isexe) | 2.0.0 (2017/03) | 4.0.0 (2026/02) | ❓ | [2026/02](https://github.com/isaacs/isexe) | 8.9y | ISC |
|  | ⚠️ | 6.1/10 | ✅ | [npm/jest-worker](https://github.com/facebook/jest) | 27.5.1 (2022/02) | 30.5.1 (2026/09) | ❓ | [2026/09](https://github.com/facebook/jest) | 4.6y | MIT |
|  | ✅ | 7.2/10 | ✅ | [npm/jose](https://github.com/panva/jose) | 6.2.12 (2026/09) | 6.2.12 (2026/09) | ❓ | [2026/09](https://github.com/panva/jose) | 0.0y | MIT |
| 🚩 | ✅ | 2.1/10 | ✅ | [npm/json-buffer](https://github.com/dominictarr/json-buffer) | 3.0.1 (2018/09) | 3.0.1 (2018/09) | ❓ | [2018/10](https://github.com/dominictarr/json-buffer) | 0.0y | MIT |
| ⚠️ | ✅ | 3.2/10 | ✅ | [npm/json-schema-to-ts](https://github.com/ThomasAribart/json-schema-to-ts) | 3.1.1 (2024/08) | 3.1.1 (2024/08) | ❓ | [2026/05](https://github.com/ThomasAribart/json-schema-to-ts) | 0.0y | MIT |
| 🚩 | ✅ | 3/10 | ✅ | [npm/json-schema-traverse](https://github.com/epoberezkin/json-schema-traverse) | 1.0.0 (2020/12) | 1.0.0 (2020/12) | ❓ | [2021/07](https://github.com/epoberezkin/json-schema-traverse) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/json-schema-typed](https://github.com/RemyRylan/json-schema-typed) | 8.0.2 (2025/11) | 8.0.2 (2025/11) | ❓ | [2025/11](https://github.com/RemyRylan/json-schema-typed) | 0.0y | BSD-2-Clause |
| 🚩 | ✅ | 1.9/10 | ✅ | [npm/json-stable-stringify-without-jsonify](https://github.com/samn/json-stable-stringify) | 1.0.1 (2016/12) | 1.0.1 (2016/12) | ❓ | [2021/04](https://github.com/samn/json-stable-stringify) | 0.0y | MIT |
|  | ⚠️ | 7.1/10 | ✅ | [npm/keyv](https://github.com/jaredwray/keyv) | 4.5.4 (2023/10) | 5.6.0 (2026/01) | ❓ | [2026/09](https://github.com/jaredwray/keyv) | 2.3y | MIT |
| 🚩 | ✅ | 2/10 | ✅ | [npm/levn](https://github.com/gkz/levn) | 0.4.1 (2020/04) | 0.4.1 (2020/04) | ❓ | [2023/07](https://github.com/gkz/levn) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/lightningcss-darwin-arm64](https://github.com/parcel-bundler/lightningcss) | 1.33.0 (2026/07) | 1.33.0 (2026/07) | ❓ | [2026/09](https://github.com/parcel-bundler/lightningcss) | 0.0y | MPL-2.0 |
|  | ✅ | ❓ | ✅ | [npm/lightningcss](https://github.com/parcel-bundler/lightningcss) | 1.33.0 (2026/07) | 1.33.0 (2026/07) | ❓ | [2026/09](https://github.com/parcel-bundler/lightningcss) | 0.0y | MPL-2.0 |
|  | ⚠️ | 3.7/10 | ✅ | [npm/locate-path](https://github.com/sindresorhus/locate-path) | 6.0.0 (2020/08) | 8.0.0 (2025/09) | ❓ | [2026/09](https://github.com/sindresorhus/locate-path) | 5.1y | MIT |
| 🚩 | ✅ | 7.5/10 | ✅ | [npm/lodash.camelcase](https://github.com/lodash/lodash) | 4.3.0 (2016/08) | 4.3.0 (2016/08) | ❓ | [2026/09](https://github.com/lodash/lodash) | 0.0y | MIT |
|  | ✅ | 3.1/10 | ✅ | [npm/long](https://github.com/dcodeIO/long.js) | 5.3.2 (2025/04) | 5.3.2 (2025/04) | ❓ | [2026/09](https://github.com/dcodeIO/long.js) | 0.0y | Apache-2.0 |
|  | ✅ | 4.5/10 | ✅ | [npm/magic-string](https://github.com/Rich-Harris/magic-string) | 1.4.1 (2026/09) | 1.4.1 (2026/09) | ❓ | [2026/09](https://github.com/Rich-Harris/magic-string) | 0.0y | MIT |
| ⚠️ | ✅ | ❓ | ✅ | [npm/math-intrinsics](https://github.com/es-shims/math-intrinsics) | 1.1.0 (2024/12) | 1.1.0 (2024/12) | ❓ | [2024/12](https://github.com/es-shims/math-intrinsics) | 0.0y | MIT |
|  | ⚠️ | 5.6/10 | ✅ | [npm/media-typer](https://github.com/jshttp/media-typer) | 1.1.1 (2026/07) | 2.0.0 (2026/05) | ❓ | [2026/07](https://github.com/jshttp/media-typer) | 0.0y | MIT |
|  | ✅ | 5.5/10 | ✅ | [npm/memfs](https://github.com/streamich/memfs) | 4.78.1 (2026/09) | 4.78.1 (2026/09) | ❓ | [2026/09](https://github.com/streamich/memfs) | 0.0y | Apache-2.0 |
| ⚠️ | ✅ | 3.4/10 | ✅ | [npm/merge-descriptors](https://github.com/sindresorhus/merge-descriptors) | 2.0.0 (2023/11) | 2.0.0 (2023/11) | ❓ | [2026/09](https://github.com/sindresorhus/merge-descriptors) | 0.0y | MIT |
| 🚩 | ✅ | 2.5/10 | ✅ | [npm/merge-stream](https://github.com/grncdr/merge-stream) | 2.0.0 (2019/05) | 2.0.0 (2019/05) | ❓ | [2019/06](https://github.com/grncdr/merge-stream) | 0.0y | MIT |
| ⚠️ | ✅ | 5.8/10 | ✅ | [npm/mime-db](https://github.com/jshttp/mime-db) | 1.54.0 (2025/03) | 1.54.0 (2025/03) | ❓ | [2026/09](https://github.com/jshttp/mime-db) | 0.0y | MIT |
|  | ✅ | 6.4/10 | ✅ | [npm/mime-types](https://github.com/jshttp/mime-types) | 3.0.2 (2025/11) | 3.0.2 (2025/11) | ❓ | [2026/06](https://github.com/jshttp/mime-types) | 0.0y | MIT |
|  | ✅ | 5.6/10 | ✅ | [npm/minimatch](https://github.com/isaacs/minimatch) | 10.2.6 (2026/07) | 10.2.6 (2026/07) | ❓ | [2026/07](https://github.com/isaacs/minimatch) | 0.0y | BlueOak-1.0.0 |
|  | ✅ | ❓ | ✅ | [npm/minimizer-webpack-plugin](https://github.com/webpack/minimizer-webpack-plugin) | 5.11.0 (2026/09) | 5.11.0 (2026/09) | ❓ | [2026/09](https://github.com/webpack/minimizer-webpack-plugin) | 0.0y | MIT |
| 🚩 | ✅ | 5/10 | ✅ | [npm/ms](https://github.com/vercel/ms) | 3.0.0-canary.1 (2021/09) | 2.1.3 (2020/12) | ❓ | [2026/05](https://github.com/vercel/ms) | 0.0y | MIT |
|  | ⚠️ | 6.5/10 | ✅ | [npm/nanoid](https://github.com/ai/nanoid) | 3.3.19 (2026/09) | 6.0.1 (2026/08) | ❓ | [2026/09](https://github.com/ai/nanoid) | 0.0y | MIT |
| 🚩 | ✅ | 3.9/10 | ✅ | [npm/natural-compare](https://github.com/litejs/natural-compare-lite) | 1.4.0 (2016/07) | 1.4.0 (2016/07) | ❓ | [2026/09](https://github.com/litejs/natural-compare-lite) | 0.0y | MIT |
|  | ✅ | 6.2/10 | ✅ | [npm/negotiator](https://github.com/jshttp/negotiator) | 1.1.0 (2026/08) | 1.1.0 (2026/08) | ❓ | [2026/08](https://github.com/jshttp/negotiator) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/nexus-rpc](https://github.com/nexus-rpc/sdk-typescript) | 0.0.3 (2026/08) | 0.0.3 (2026/08) | ❓ | [2026/08](https://github.com/nexus-rpc/sdk-typescript) | 0.0y | MIT |
|  | ✅ | 4/10 | ✅ | [npm/node-releases](https://github.com/chicoxyzzy/node-releases) | 2.0.56 (2026/09) | 2.0.56 (2026/09) | ❓ | [2026/09](https://github.com/chicoxyzzy/node-releases) | 0.0y | MIT |
| 🚩 | ✅ | 3.7/10 | ✅ | [npm/object-assign](https://github.com/sindresorhus/object-assign) | 4.1.1 (2017/01) | 4.1.1 (2017/01) | ❓ | [2026/09](https://github.com/sindresorhus/object-assign) | 0.0y | MIT |
| ⚠️ | ✅ | 4/10 | ✅ | [npm/object-inspect](https://github.com/inspect-js/object-inspect) | 1.13.4 (2025/02) | 1.13.4 (2025/02) | ❓ | [2026/04](https://github.com/inspect-js/object-inspect) | 0.0y | MIT |
|  | ⚠️ | ❓ | ✅ | [npm/obug](https://github.com/sxzz/obug) | 2.2.1 (2026/09) | 3.0.0 (2026/09) | ❓ | [2026/09](https://github.com/sxzz/obug) | 0.0y | MIT |
| 🚩 | ✅ | 5.6/10 | ✅ | [npm/on-finished](https://github.com/jshttp/on-finished) | 2.4.1 (2022/02) | 2.4.1 (2022/02) | ❓ | [2026/05](https://github.com/jshttp/on-finished) | 0.0y | MIT |
| 🚩 | ✅ | 3.6/10 | ✅ | [npm/once](https://github.com/isaacs/once) | 1.4.0 (2016/09) | 1.4.0 (2016/09) | ❓ | [2025/10](https://github.com/isaacs/once) | 0.0y | ISC |
| ⚠️ | ✅ | 2.1/10 | ✅ | [npm/optionator](https://github.com/gkz/optionator) | 0.9.4 (2024/04) | 0.9.4 (2024/04) | ❓ | [2024/04](https://github.com/gkz/optionator) | 0.0y | MIT |
|  | ⚠️ | 3.7/10 | ✅ | [npm/p-limit](https://github.com/sindresorhus/p-limit) | 3.1.0 (2020/11) | 7.3.3 (2026/09) | ❓ | [2026/09](https://github.com/sindresorhus/p-limit) | 5.8y | MIT |
|  | ⚠️ | 3.7/10 | ✅ | [npm/p-locate](https://github.com/sindresorhus/p-locate) | 5.0.0 (2020/08) | 7.0.0 (2026/02) | ❓ | [2026/09](https://github.com/sindresorhus/p-locate) | 5.5y | MIT |
| 🚩 | ✅ | 5.2/10 | ✅ | [npm/parseurl](https://github.com/pillarjs/parseurl) | 1.3.3 (2019/04) | 1.3.3 (2019/04) | ❓ | [2026/03](https://github.com/pillarjs/parseurl) | 0.0y | MIT |
| 🚩 | ⚠️ | 3.7/10 | ✅ | [npm/path-exists](https://github.com/sindresorhus/path-exists) | 4.0.0 (2019/04) | 5.0.0 (2021/08) | ❓ | [2026/09](https://github.com/sindresorhus/path-exists) | 2.4y | MIT |
| 🚩 | ⚠️ | 3.8/10 | ✅ | [npm/path-key](https://github.com/sindresorhus/path-key) | 3.1.1 (2019/11) | 4.0.0 (2021/04) | ❓ | [2026/09](https://github.com/sindresorhus/path-key) | 1.4y | MIT |
|  | ✅ | 6.2/10 | ✅ | [npm/path-to-regexp](https://github.com/pillarjs/path-to-regexp) | 8.4.2 (2026/04) | 8.4.2 (2026/04) | ❓ | [2026/09](https://github.com/pillarjs/path-to-regexp) | 0.0y | MIT |
| ⚠️ | ✅ | 3.2/10 | ✅ | [npm/picocolors](https://github.com/alexeyraspopov/picocolors) | 1.1.1 (2024/10) | 1.1.1 (2024/10) | ❓ | [2024/11](https://github.com/alexeyraspopov/picocolors) | 0.0y | ISC |
|  | ✅ | 6.7/10 | ✅ | [npm/picomatch](https://github.com/micromatch/picomatch) | 4.0.7 (2026/08) | 4.0.7 (2026/08) | ❓ | [2026/08](https://github.com/micromatch/picomatch) | 0.0y | MIT |
|  | ⚠️ | 3.6/10 | ✅ | [npm/pkce-challenge](https://github.com/crouchcd/pkce-challenge) | 5.0.1 (2025/11) | 6.0.0 (2026/02) | ❓ | [2026/07](https://github.com/crouchcd/pkce-challenge) | 0.2y | MIT |
|  | ✅ | 7.5/10 | ✅ | [npm/postcss](https://github.com/postcss/postcss) | 8.5.28 (2026/09) | 8.5.28 (2026/09) | ❓ | [2026/09](https://github.com/postcss/postcss) | 0.0y | MIT |
| 🚩 | ✅ | 2.1/10 | ✅ | [npm/prelude-ls](https://github.com/gkz/prelude-ls) | 1.2.1 (2020/04) | 1.2.1 (2020/04) | ❓ | [2026/05](https://github.com/gkz/prelude-ls) | 0.0y | MIT |
|  | ⚠️ | 6.2/10 | ✅ | [npm/protobufjs](https://github.com/protobufjs/protobuf.js) | 7.6.6 (2026/08) | 8.8.0 (2026/08) | ❓ | [2026/09](https://github.com/protobufjs/protobuf.js) | 0.0y | BSD-3-Clause |
|  | ✅ | 5.7/10 | ✅ | [npm/proxy-addr](https://github.com/jshttp/proxy-addr) | 2.0.8 (2026/09) | 2.0.8 (2026/09) | ❓ | [2026/09](https://github.com/jshttp/proxy-addr) | 0.0y | MIT |
| ⚠️ | ✅ | 3.2/10 | ✅ | [npm/punycode](https://github.com/mathiasbynens/punycode.js) | 2.3.1 (2023/10) | 2.3.1 (2023/10) | ❓ | [2024/04](https://github.com/mathiasbynens/punycode.js) | 0.0y | MIT |
|  | ✅ | 5.4/10 | ✅ | [npm/qs](https://github.com/ljharb/qs) | 6.16.0 (2026/08) | 6.16.0 (2026/08) | ❓ | [2026/09](https://github.com/ljharb/qs) | 0.0y | BSD-3-Clause |
|  | ✅ | 5.5/10 | ✅ | [npm/range-parser](https://github.com/jshttp/range-parser) | 1.3.0 (2026/06) | 1.3.0 (2026/06) | ❓ | [2026/06](https://github.com/jshttp/range-parser) | 0.0y | MIT |
|  | ⚠️ | 6.8/10 | ✅ | [npm/raw-body](https://github.com/stream-utils/raw-body) | 3.0.2 (2025/11) | 4.0.0 (2026/07) | ❓ | [2026/09](https://github.com/stream-utils/raw-body) | 0.6y | MIT |
| 🚩 | ✅ | 2.3/10 | ✅ | [npm/require-directory](https://github.com/troygoode/node-require-directory) | 2.1.1 (2015/05) | 2.1.1 (2015/05) | ❓ | [2021/12](https://github.com/troygoode/node-require-directory) | 0.0y | MIT |
| 🚩 | ✅ | 2.3/10 | ✅ | [npm/require-from-string](https://github.com/floatdrop/require-from-string) | 2.0.2 (2018/04) | 2.0.2 (2018/04) | ❓ | [2018/04](https://github.com/floatdrop/require-from-string) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/rolldown](https://github.com/rolldown/rolldown) | 1.2.9 (2026/09) | 1.2.9 (2026/09) | ❓ | [2026/09](https://github.com/rolldown/rolldown) | 0.0y | MIT |
|  | ✅ | 6.4/10 | ✅ | [npm/router](https://github.com/pillarjs/router) | 2.2.0 (2025/03) | 2.2.0 (2025/03) | ❓ | [2026/06](https://github.com/pillarjs/router) | 0.0y | MIT |
| ⚠️ | ✅ | 8.4/10 | ✅ | [npm/rxjs](https://github.com/reactivex/rxjs) | 7.8.2 (2025/02) | 7.8.2 (2025/02) | ❓ | [2026/08](https://github.com/reactivex/rxjs) | 0.0y | Apache-2.0 |
| 🚩 | ✅ | 2.1/10 | ✅ | [npm/safer-buffer](https://github.com/ChALkeR/safer-buffer) | 2.1.2 (2018/04) | 2.1.2 (2018/04) | ❓ | [2021/04](https://github.com/ChALkeR/safer-buffer) | 0.0y | MIT |
|  | ✅ | 4.2/10 | ✅ | [npm/schema-utils](https://github.com/webpack/schema-utils) | 4.5.0 (2026/09) | 4.5.0 (2026/09) | ❓ | [2026/09](https://github.com/webpack/schema-utils) | 0.0y | MIT |
|  | ✅ | 6.4/10 | ✅ | [npm/semver](https://github.com/npm/node-semver) | 7.8.5 (2026/06) | 7.8.5 (2026/06) | ❓ | [2026/09](https://github.com/npm/node-semver) | 0.0y | ISC |
|  | ✅ | 6.3/10 | ✅ | [npm/send](https://github.com/pillarjs/send) | 1.2.1 (2025/12) | 1.2.1 (2025/12) | ❓ | [2026/06](https://github.com/pillarjs/send) | 0.0y | MIT |
|  | ✅ | 6.5/10 | ✅ | [npm/serve-static](https://github.com/expressjs/serve-static) | 2.2.1 (2025/12) | 2.2.1 (2025/12) | ❓ | [2026/01](https://github.com/expressjs/serve-static) | 0.0y | MIT |
| 🚩 | ✅ | 3.2/10 | ✅ | [npm/setprototypeof](https://github.com/wesleytodd/setprototypeof) | 1.2.0 (2019/07) | 1.2.0 (2019/07) | ❓ | [2022/06](https://github.com/wesleytodd/setprototypeof) | 0.0y | ISC |
| 🚩 | ✅ | 3.1/10 | ✅ | [npm/shebang-command](https://github.com/kevva/shebang-command) | 2.0.0 (2019/09) | 2.0.0 (2019/09) | ❓ | [2021/08](https://github.com/kevva/shebang-command) | 0.0y | MIT |
| 🚩 | ⚠️ | 3.7/10 | ✅ | [npm/shebang-regex](https://github.com/sindresorhus/shebang-regex) | 3.0.0 (2019/04) | 4.0.0 (2021/08) | ❓ | [2026/09](https://github.com/sindresorhus/shebang-regex) | 2.3y | MIT |
|  | ✅ | ❓ | ✅ | [npm/side-channel-list](https://github.com/ljharb/side-channel-list) | 1.0.1 (2026/04) | 1.0.1 (2026/04) | ❓ | [2026/04](https://github.com/ljharb/side-channel-list) | 0.0y | MIT |
| ⚠️ | ✅ | ❓ | ✅ | [npm/side-channel-map](https://github.com/ljharb/side-channel-map) | 1.0.1 (2024/12) | 1.0.1 (2024/12) | ❓ | [2025/12](https://github.com/ljharb/side-channel-map) | 0.0y | MIT |
| ⚠️ | ✅ | ❓ | ✅ | [npm/side-channel-weakmap](https://github.com/ljharb/side-channel-weakmap) | 1.0.2 (2024/12) | 1.0.2 (2024/12) | ❓ | [2025/12](https://github.com/ljharb/side-channel-weakmap) | 0.0y | MIT |
|  | ✅ | 4.4/10 | ✅ | [npm/side-channel](https://github.com/ljharb/side-channel) | 1.1.1 (2026/06) | 1.1.1 (2026/06) | ❓ | [2026/06](https://github.com/ljharb/side-channel) | 0.0y | MIT |
| 🚩 | ✅ | 2.1/10 | ✅ | [npm/siginfo](https://github.com/emilbayes/siginfo) | 2.0.0 (2020/06) | 2.0.0 (2020/06) | ❓ | [2020/06](https://github.com/emilbayes/siginfo) | 0.0y | ISC |
| ⚠️ | ✅ | 2.8/10 | ✅ | [npm/source-map-js](https://github.com/7rulnik/source-map-js) | 1.2.1 (2024/09) | 1.2.1 (2024/09) | ❓ | [2026/05](https://github.com/7rulnik/source-map-js) | 0.0y | BSD-3-Clause |
| 🚩 | ✅ | 5.2/10 | ✅ | [npm/source-map-loader](https://github.com/webpack-contrib/source-map-loader) | 5.0.0 (2024/01) | 5.0.0 (2024/01) | ❓ | [2025/08](https://github.com/webpack-contrib/source-map-loader) | 0.0y | MIT |
| 🚩 | ✅ | 2.3/10 | ✅ | [npm/source-map-support](https://github.com/evanw/node-source-map-support) | 0.5.21 (2021/11) | 0.5.21 (2021/11) | ❓ | [2024/08](https://github.com/evanw/node-source-map-support) | 0.0y | MIT |
|  | ⚠️ | 5.2/10 | ✅ | [npm/source-map](https://github.com/mozilla/source-map) | 0.7.6 (2025/07) | 0.8.0 (2026/07) | ❓ | [2026/09](https://github.com/mozilla/source-map) | 1.0y | BSD-3-Clause |
| 🚩 | ✅ | 1.5/10 | ✅ | [npm/stackback](https://github.com/shtylman/node-stackback) | 0.0.2 (2012/10) | 0.0.2 (2012/10) | ❓ | [2013/10](https://github.com/shtylman/node-stackback) | 0.0y | MIT |
|  | ✅ | 5.2/10 | ✅ | [npm/standardwebhooks](https://github.com/standard-webhooks/standard-webhooks) | 1.1.1 (2026/08) | 1.1.1 (2026/08) | ❓ | [2026/09](https://github.com/standard-webhooks/standard-webhooks) | 0.0y | MIT |
|  | ✅ | 6.1/10 | ✅ | [npm/statuses](https://github.com/jshttp/statuses) | 2.0.2 (2025/06) | 2.0.2 (2025/06) | ❓ | [2026/01](https://github.com/jshttp/statuses) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/std-env](https://github.com/unjs/std-env) | 4.2.0 (2026/07) | 4.2.0 (2026/07) | ❓ | [2026/09](https://github.com/unjs/std-env) | 0.0y | MIT |
|  | ⚠️ | 3.9/10 | ✅ | [npm/string-width](https://github.com/sindresorhus/string-width) | 4.2.3 (2021/09) | 8.2.2 (2026/07) | ❓ | [2026/09](https://github.com/sindresorhus/string-width) | 4.8y | MIT |
|  | ⚠️ | 3.7/10 | ✅ | [npm/strip-ansi](https://github.com/chalk/strip-ansi) | 6.0.1 (2021/09) | 7.2.0 (2026/02) | ❓ | [2026/09](https://github.com/chalk/strip-ansi) | 4.4y | MIT |
|  | ⚠️ | 4.2/10 | ✅ | [npm/supports-color](https://github.com/chalk/supports-color) | 8.1.1 (2021/01) | 11.0.0 (2026/07) | ❓ | [2026/09](https://github.com/chalk/supports-color) | 5.5y | MIT |
|  | ✅ | ❓ | ✅ | [npm/swc-loader](https://github.com/swc-project/pkgs) | 0.2.7 (2026/01) | 0.2.7 (2026/01) | ❓ | [2026/08](https://github.com/swc-project/pkgs) | 0.0y | MIT |
|  | ✅ | 5.6/10 | ✅ | [npm/tapable](https://github.com/webpack/tapable) | 2.3.3 (2026/04) | 2.3.3 (2026/04) | ❓ | [2026/09](https://github.com/webpack/tapable) | 0.0y | MIT |
|  | ✅ | 5.8/10 | ✅ | [npm/terser](https://github.com/terser/terser) | 5.51.2 (2026/08) | 5.51.2 (2026/08) | ❓ | [2026/09](https://github.com/terser/terser) | 0.0y | BSD-2-Clause |
|  | ✅ | ❓ | ✅ | [npm/thingies](https://github.com/streamich/thingies) | 2.6.1 (2026/07) | 2.6.1 (2026/07) | ❓ | [2026/07](https://github.com/streamich/thingies) | 0.0y | MIT |
|  | ⚠️ | ❓ | ✅ | [npm/tinybench](https://tinylibs/tinybench) | 6.1.4 (2026/08) | 6.2.0 (2026/09) | ❓ | ❓ | 0.0y | MIT |
|  | ⚠️ | ❓ | ✅ | [npm/tinyexec](https://github.com/tinylibs/tinyexec) | 1.3.0 (2026/08) | 1.3.1 (2026/09) | ❓ | [2026/09](https://github.com/tinylibs/tinyexec) | 0.1y | MIT |
|  | ✅ | ❓ | ✅ | [npm/tinyglobby](https://github.com/SuperchupuDev/tinyglobby) | 0.2.17 (2026/05) | 0.2.17 (2026/05) | ❓ | [2026/09](https://github.com/SuperchupuDev/tinyglobby) | 0.0y | MIT |
| 🚩 | ✅ | 2.8/10 | ✅ | [npm/toidentifier](https://github.com/component/toidentifier) | 1.0.1 (2021/11) | 1.0.1 (2021/11) | ❓ | [2023/12](https://github.com/component/toidentifier) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/tree-dump](https://github.com/streamich/tree-dump) | 1.1.0 (2025/09) | 1.1.0 (2025/09) | ❓ | [2025/09](https://github.com/streamich/tree-dump) | 0.0y | Apache-2.0 |
| ⚠️ | ✅ | ❓ | ✅ | [npm/ts-algebra](https://github.com/ThomasAribart/ts-algebra) | 2.0.0 (2024/05) | 2.0.0 (2024/05) | ❓ | [2025/02](https://github.com/ThomasAribart/ts-algebra) | 0.0y | MIT |
|  | ✅ | 4.6/10 | ✅ | [npm/ts-api-utils](https://github.com/JoshuaKGoldberg/ts-api-utils) | 2.5.0 (2026/03) | 2.5.0 (2026/03) | ❓ | [2026/07](https://github.com/JoshuaKGoldberg/ts-api-utils) | 0.0y | MIT |
| ⚠️ | ✅ | 6.9/10 | ✅ | [npm/tslib](https://github.com/Microsoft/tslib) | 2.8.1 (2024/10) | 2.8.1 (2024/10) | ❓ | [2026/06](https://github.com/Microsoft/tslib) | 0.0y | 0BSD |
| 🚩 | ✅ | 2.3/10 | ✅ | [npm/type-check](https://github.com/gkz/type-check) | 0.4.0 (2020/04) | 0.4.0 (2020/04) | ❓ | [2023/07](https://github.com/gkz/type-check) | 0.0y | MIT |
|  | ⚠️ | 5.9/10 | ✅ | [npm/type-is](https://github.com/jshttp/type-is) | 2.1.0 (2026/05) | 3.0.0 (2026/09) | ❓ | [2026/09](https://github.com/jshttp/type-is) | 0.4y | MIT |
|  | ✅ | 6.7/10 | ✅ | [npm/typescript-eslint](https://github.com/typescript-eslint/typescript-eslint) | 8.70.1 (2026/09) | 8.70.1 (2026/09) | ❓ | [2026/09](https://github.com/typescript-eslint/typescript-eslint) | 0.0y | MIT |
|  | ⚠️ | 8.2/10 | ✅ | [npm/typescript](https://github.com/microsoft/TypeScript) | 5.9.3 (2025/09) | 7.0.2 (2026/07) | ❓ | [2026/09](https://github.com/microsoft/TypeScript) | 0.8y | Apache-2.0 |
|  | ⚠️ | 8.1/10 | ✅ | [npm/undici-types](https://github.com/nodejs/undici) | 7.18.2 (2026/01) | 8.10.2 (2026/09) | ❓ | [2026/09](https://github.com/nodejs/undici) | 0.7y | MIT |
|  | ✅ | 2.9/10 | ✅ | [npm/unionfs](https://github.com/streamich/unionfs) | 4.6.0 (2025/07) | 4.6.0 (2025/07) | ❓ | [2025/09](https://github.com/streamich/unionfs) | 0.0y | - |
| 🚩 | ✅ | 2/10 | ✅ | [npm/unpipe](https://github.com/stream-utils/unpipe) | 1.0.0 (2015/06) | 1.0.0 (2015/06) | ❓ | [2020/12](https://github.com/stream-utils/unpipe) | 0.0y | MIT |
|  | ✅ | 5.5/10 | ✅ | [npm/update-browserslist-db](https://github.com/browserslist/update-db) | 1.3.3 (2026/09) | 1.3.3 (2026/09) | ❓ | [2026/09](https://github.com/browserslist/update-db) | 0.0y | MIT |
| 🚩 | ✅ | 2.4/10 | ✅ | [npm/uri-js](https://github.com/garycourt/uri-js) | 4.4.1 (2021/01) | 4.4.1 (2021/01) | ❓ | [2023/12](https://github.com/garycourt/uri-js) | 0.0y | BSD-2-Clause |
|  | ⚠️ | 4.4/10 | ✅ | [npm/uuid](https://github.com/uuidjs/uuid) | 11.1.1 (2026/04) | 14.0.2 (2026/08) | ❓ | [2026/09](https://github.com/uuidjs/uuid) | 0.3y | MIT |
| 🚩 | ✅ | 5.4/10 | ✅ | [npm/vary](https://github.com/jshttp/vary) | 1.1.2 (2017/09) | 1.1.2 (2017/09) | ❓ | [2026/04](https://github.com/jshttp/vary) | 0.0y | MIT |
|  | ✅ | 6.8/10 | ✅ | [npm/vite](https://github.com/vitejs/vite) | 8.3.0 (2026/09) | 8.3.0 (2026/09) | ❓ | [2026/09](https://github.com/vitejs/vite) | 0.0y | MIT |
|  | ✅ | ❓ | ✅ | [npm/vitest](https://github.com/vitest-dev/vitest) | 5.0.1 (2026/09) | 5.0.1 (2026/09) | ❓ | [2026/09](https://github.com/vitest-dev/vitest) | 0.0y | MIT |
|  | ✅ | 5.9/10 | ✅ | [npm/watchpack](https://github.com/webpack/watchpack) | 2.5.2 (2026/06) | 2.5.2 (2026/06) | ❓ | [2026/09](https://github.com/webpack/watchpack) | 0.0y | MIT |
|  | ✅ | 5.6/10 | ✅ | [npm/webpack-sources](https://github.com/webpack/webpack-sources) | 3.5.1 (2026/07) | 3.5.1 (2026/07) | ❓ | [2026/09](https://github.com/webpack/webpack-sources) | 0.0y | MIT |
|  | ✅ | 5.6/10 | ✅ | [npm/webpack](https://github.com/webpack/webpack) | 5.111.1 (2026/09) | 5.111.1 (2026/09) | ❓ | [2026/09](https://github.com/webpack/webpack) | 0.0y | MIT |
|  | ⚠️ | 5.7/10 | ✅ | [npm/which](https://github.com/isaacs/node-which) | 2.0.2 (2019/11) | 7.0.0 (2026/05) | ❓ | [2026/07](https://github.com/isaacs/node-which) | 6.5y | ISC |
| ⚠️ | ⚠️ | 3.8/10 | ✅ | [npm/why-is-node-running](https://github.com/mafintosh/why-is-node-running) | 2.3.0 (2024/07) | 3.2.2 (2025/01) | ❓ | [2025/01](https://github.com/mafintosh/why-is-node-running) | 0.5y | MIT |
| 🚩 | ✅ | 3/10 | ✅ | [npm/word-wrap](https://github.com/jonschlinkert/word-wrap) | 1.2.5 (2023/07) | 1.2.5 (2023/07) | ❓ | [2024/04](https://github.com/jonschlinkert/word-wrap) | 0.0y | MIT |
|  | ⚠️ | 4/10 | ✅ | [npm/wrap-ansi](https://github.com/chalk/wrap-ansi) | 7.0.0 (2020/04) | 10.0.2 (2026/09) | ❓ | [2026/09](https://github.com/chalk/wrap-ansi) | 6.4y | MIT |
| 🚩 | ✅ | 3.7/10 | ✅ | [npm/wrappy](https://github.com/npm/wrappy) | 1.0.2 (2016/05) | 1.0.2 (2016/05) | ❓ | [2024/02](https://github.com/npm/wrappy) | 0.0y | ISC |
| 🚩 | ✅ | 4.2/10 | ✅ | [npm/y18n](https://github.com/yargs/y18n) | 5.0.8 (2021/04) | 5.0.8 (2021/04) | ❓ | [2026/09](https://github.com/yargs/y18n) | 0.0y | ISC |
|  | ⚠️ | 4.8/10 | ✅ | [npm/yargs-parser](https://github.com/yargs/yargs-parser) | 21.1.1 (2022/08) | 22.0.0 (2025/05) | ❓ | [2026/09](https://github.com/yargs/yargs-parser) | 2.8y | ISC |
|  | ⚠️ | 6.8/10 | ✅ | [npm/yargs](https://github.com/yargs/yargs) | 17.7.3 (2026/06) | 18.2.0 (2026/09) | ❓ | [2026/09](https://github.com/yargs/yargs) | 0.3y | MIT |
|  | ⚠️ | 3.6/10 | ✅ | [npm/yocto-queue](https://github.com/sindresorhus/yocto-queue) | 0.1.0 (2020/11) | 1.2.2 (2025/11) | ❓ | [2026/09](https://github.com/sindresorhus/yocto-queue) | 5.0y | MIT |
| 🚩 | ✅ | 2.1/10 | ✅ | [npm/zod-to-json-schema](https://github.com/StefanTerdell/zod-to-json-schema) | 3.25.2 (2026/03) | 3.25.2 (2026/03) | ❓ | [2026/03](https://github.com/StefanTerdell/zod-to-json-schema) | 0.0y | ISC |
|  | ✅ | 5.1/10 | ✅ | [npm/zod](https://github.com/colinhacks/zod) | 4.6.5 (2026/09) | 4.6.5 (2026/09) | ❓ | [2026/09](https://github.com/colinhacks/zod) | 0.0y | MIT |

**Transitive findings** (pulled in by a direct dependency):
- `npm/@humanwhocodes/module-importer@1.0.1` via `npm/eslint`
- `npm/@js-sdsl/ordered-map@4.4.2` via `npm/@temporalio/client`
- `npm/@protobufjs/aspromise@1.1.2` via `npm/@temporalio/client`
- `npm/@protobufjs/base64@1.1.2` via `npm/@temporalio/client`
- `npm/@protobufjs/float@1.0.2` via `npm/@temporalio/client`
- `npm/@protobufjs/path@1.1.2` via `npm/@temporalio/client`
- `npm/@protobufjs/pool@1.1.0` via `npm/@temporalio/client`
- `npm/@xtuc/ieee754@1.2.0` via `npm/@temporalio/worker`
- `npm/@xtuc/long@4.2.2` via `npm/@temporalio/worker`
- `npm/abort-controller@3.0.0` via `npm/@temporalio/client`
- `npm/acorn-jsx@5.3.2` via `npm/eslint`
- `npm/ajv-keywords@5.1.0` via `npm/@temporalio/worker`
- `npm/buffer-from@1.1.2` via `npm/vitest`
- `npm/bytes@3.1.2` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/deep-is@0.1.4` via `npm/eslint`
- `npm/depd@2.0.0` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/ee-first@1.1.1` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/escape-html@1.0.3` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/escape-string-regexp@4.0.0` via `npm/eslint`
- `npm/esrecurse@4.3.0` via `npm/eslint`
- `npm/estraverse@5.3.0` via `npm/eslint`
- `npm/estree-walker@3.0.3` via `npm/vitest`
- `npm/esutils@2.0.3` via `npm/eslint`
- `npm/etag@1.8.1` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/event-target-shim@5.0.1` via `npm/@temporalio/client`
- `npm/events@3.3.0` via `npm/@temporalio/worker`
- `npm/fast-deep-equal@3.1.3` via `npm/eslint`
- `npm/fast-levenshtein@2.0.6` via `npm/eslint`
- `npm/fast-sha256@1.3.0` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/forwarded@0.2.0` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/fsevents@2.3.3` via `npm/vitest`
- `npm/get-caller-file@2.0.5` via `npm/@temporalio/client`
- `npm/glob-parent@6.0.2` via `npm/eslint`
- `npm/graceful-fs@4.2.11` via `npm/@temporalio/worker`
- `npm/has-flag@4.0.0` via `npm/@temporalio/worker`
- `npm/hyperdyperid@1.2.0` via `npm/@temporalio/worker`
- `npm/imurmurhash@0.1.4` via `npm/eslint`
- `npm/inherits@2.0.4` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/is-extglob@2.1.1` via `npm/eslint`
- `npm/is-glob@4.0.3` via `npm/eslint`
- `npm/is-promise@4.0.0` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/json-buffer@3.0.1` via `npm/eslint`
- `npm/json-schema-traverse@1.0.0` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/json-stable-stringify-without-jsonify@1.0.1` via `npm/eslint`
- `npm/levn@0.4.1` via `npm/eslint`
- `npm/lodash.camelcase@4.3.0` via `npm/@temporalio/client`
- `npm/merge-stream@2.0.0` via `npm/@temporalio/worker`
- `npm/ms@3.0.0-canary.1` via `npm/@temporalio/common`
- `npm/natural-compare@1.4.0` via `npm/eslint`
- `npm/object-assign@4.1.1` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/on-finished@2.4.1` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/once@1.4.0` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/parseurl@1.3.3` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/path-exists@4.0.0` via `npm/eslint`
- `npm/path-key@3.1.1` via `npm/eslint`
- `npm/prelude-ls@1.2.1` via `npm/eslint`
- `npm/require-directory@2.1.1` via `npm/@temporalio/client`
- `npm/require-from-string@2.0.2` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/safer-buffer@2.1.2` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/setprototypeof@1.2.0` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/shebang-command@2.0.0` via `npm/eslint`
- `npm/shebang-regex@3.0.0` via `npm/eslint`
- `npm/siginfo@2.0.0` via `npm/vitest`
- `npm/source-map-loader@5.0.0` via `npm/@temporalio/worker`
- `npm/source-map-support@0.5.21` via `npm/vitest`
- `npm/stackback@0.0.2` via `npm/vitest`
- `npm/toidentifier@1.0.1` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/type-check@0.4.0` via `npm/eslint`
- `npm/unpipe@1.0.0` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/vary@1.1.2` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/word-wrap@1.2.5` via `npm/eslint`
- `npm/wrappy@1.0.2` via `npm/@anthropic-ai/claude-agent-sdk`
- `npm/y18n@5.0.8` via `npm/@temporalio/client`
- `npm/zod-to-json-schema@3.25.2` via `npm/@anthropic-ai/claude-agent-sdk`

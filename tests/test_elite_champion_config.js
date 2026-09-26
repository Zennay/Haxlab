"use strict";

const assert = require("assert");
const fs = require("fs");
const os = require("os");
const path = require("path");

const {
  resolveEliteChampionConfig,
  futureMotionRuntimeSettings,
} = require("../tools/elite_champion_config");

const root = fs.mkdtempSync(path.join(os.tmpdir(), "haxlab-champion-config-"));
try {
  const fallbackDir = path.join(root, "fallback");
  fs.mkdirSync(fallbackDir, { recursive: true });
  fs.writeFileSync(
    path.join(fallbackDir, "runtime-model.json"),
    JSON.stringify({ schema: "fallback" }),
  );

  const missingPointer = path.join(root, "missing-current.json");
  const fallback = resolveEliteChampionConfig({
    pointerPath: missingPointer,
    fallbackModelDir: fallbackDir,
  });
  assert.strictEqual(fallback.source, "fallback");
  assert.strictEqual(
    fallback.runtime_model_path,
    path.join(fallbackDir, "runtime-model.json"),
  );

  const versionDir = path.join(root, "registry", "versions", "future-abc");
  fs.mkdirSync(versionDir, { recursive: true });
  const runtimeModelPath = path.join(versionDir, "runtime-model.json");
  fs.writeFileSync(
    runtimeModelPath,
    JSON.stringify({ schema: "haxlab-elite-js-runtime-v1" }),
  );

  const pointerPath = path.join(root, "registry", "current.json");
  fs.writeFileSync(
    pointerPath,
    JSON.stringify({
      schema: "haxlab-champion-pointer-v1",
      version_id: "future-abc",
      runtime_model_path: runtimeModelPath,
      behavior_sha256: "behavior123",
      model_sha256: "model123",
      runtime_model_sha256: "runtime123",
      runtime_config: {
        minimum_confidence: 0.6,
        minimum_ball_distance: 90,
        allowed_roles: ["am"],
      },
    }),
  );

  const resolved = resolveEliteChampionConfig({
    pointerPath,
    fallbackModelDir: fallbackDir,
  });
  assert.strictEqual(resolved.source, "registry");
  assert.strictEqual(resolved.version_id, "future-abc");
  assert.strictEqual(resolved.runtime_model_path, runtimeModelPath);
  assert.strictEqual(resolved.behavior_sha256, "behavior123");

  const future = futureMotionRuntimeSettings(resolved, {
    enabled: false,
    minimumConfidence: 0.45,
    minimumBallDistance: 80,
  });
  assert.deepStrictEqual(future, {
    enabled: true,
    minimumConfidence: 0.6,
    minimumBallDistance: 90,
    allowedRoles: ["am"],
    source: "champion_registry",
  });

  const fallbackFuture = futureMotionRuntimeSettings(fallback, {
    enabled: true,
    minimumConfidence: 0.5,
    minimumBallDistance: 100,
  });
  assert.deepStrictEqual(fallbackFuture, {
    enabled: true,
    minimumConfidence: 0.5,
    minimumBallDistance: 100,
    allowedRoles: null,
    source: "plugin_variables",
  });

  const invalidPointer = path.join(root, "invalid.json");
  fs.writeFileSync(
    invalidPointer,
    JSON.stringify({
      schema: "wrong-schema",
      runtime_model_path: runtimeModelPath,
    }),
  );
  assert.throws(
    () =>
      resolveEliteChampionConfig({
        pointerPath: invalidPointer,
        fallbackModelDir: fallbackDir,
      }),
    /unsupported HaxLab champion pointer schema/,
  );



  const invalidRolesPointer = path.join(root, "invalid-roles.json");
  fs.writeFileSync(
    invalidRolesPointer,
    JSON.stringify({
      schema: "haxlab-champion-pointer-v1",
      version_id: "future-invalid-roles",
      runtime_model_path: runtimeModelPath,
      runtime_config: {
        minimum_confidence: 0.6,
        minimum_ball_distance: 90,
        allowed_roles: ["sweeper"],
      },
    }),
  );
  const invalidRolesResolved = resolveEliteChampionConfig({
    pointerPath: invalidRolesPointer,
    fallbackModelDir: fallbackDir,
  });
  assert.throws(
    () =>
      futureMotionRuntimeSettings(invalidRolesResolved, {
        enabled: false,
        minimumConfidence: 0.45,
        minimumBallDistance: 80,
        allowedRoles: null,
      }),
    /allowed_roles contains no valid roles/,
  );
  console.log("elite champion config: ok");
} finally {
  fs.rmSync(root, { recursive: true, force: true });
}

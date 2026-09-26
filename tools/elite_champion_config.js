"use strict";

const fs = require("fs");
const path = require("path");

const POINTER_SCHEMA = "haxlab-champion-pointer-v1";
const VALIDATION_STAGE_ORDER = Object.freeze({
  promotion: 0,
  multi_replay: 10,
  canary: 20,
  live: 30,
});

function validationStageRank(stage) {
  if (stage == null || stage === "") return VALIDATION_STAGE_ORDER.promotion;
  const normalized = String(stage).trim().toLowerCase();
  if (!Object.prototype.hasOwnProperty.call(VALIDATION_STAGE_ORDER, normalized)) {
    throw new Error("unsupported HaxLab validation stage: " + String(stage));
  }
  return VALIDATION_STAGE_ORDER[normalized];
}

function resolveEliteChampionConfig({
  pointerPath,
  fallbackModelDir,
  minimumValidationStage = null,
  fsModule = fs,
}) {
  const fallbackRuntimeModelPath = path.join(
    String(fallbackModelDir),
    "runtime-model.json",
  );

  if (!pointerPath || !fsModule.existsSync(pointerPath)) {
    return {
      source: "fallback",
      version_id: null,
      pointer_path: pointerPath || null,
      runtime_model_path: fallbackRuntimeModelPath,
      runtime_config: null,
      behavior_sha256: null,
    };
  }

  const pointer = JSON.parse(fsModule.readFileSync(pointerPath, "utf8"));
  if (pointer.schema !== POINTER_SCHEMA) {
    throw new Error(
      "unsupported HaxLab champion pointer schema: " +
        String(pointer.schema || "missing"),
    );
  }

  if (minimumValidationStage != null && minimumValidationStage !== "") {
    const requiredStage = String(minimumValidationStage).trim().toLowerCase();
    const currentStage = String(
      pointer.validation_stage || "promotion",
    ).trim().toLowerCase();
    const requiredRank = validationStageRank(requiredStage);
    const currentRank = validationStageRank(currentStage);

    if (currentRank < requiredRank) {
      return {
        source: "fallback",
        version_id: null,
        pointer_path: pointerPath,
        runtime_model_path: fallbackRuntimeModelPath,
        runtime_config: null,
        behavior_sha256: null,
        fallback_reason: "registry_validation_stage_insufficient",
        blocked_registry_version_id: pointer.version_id || null,
        blocked_validation_stage: currentStage,
        minimum_validation_stage: requiredStage,
      };
    }
  }

  let runtimeModelPath = String(pointer.runtime_model_path || "");
  if (!runtimeModelPath && pointer.manifest_path) {
    const manifestPath = String(pointer.manifest_path);
    if (!fsModule.existsSync(manifestPath)) {
      throw new Error(
        "champion manifest does not exist: " + manifestPath,
      );
    }
    const manifest = JSON.parse(fsModule.readFileSync(manifestPath, "utf8"));
    const versionDir = path.dirname(manifestPath);
    runtimeModelPath = path.join(versionDir, "runtime-model.json");
  }
  if (!runtimeModelPath) {
    throw new Error(
      "champion pointer missing runtime_model_path and manifest fallback",
    );
  }
  if (!fsModule.existsSync(runtimeModelPath)) {
    throw new Error(
      "champion runtime model does not exist: " + runtimeModelPath,
    );
  }

  const runtimeConfig =
    pointer.runtime_config && typeof pointer.runtime_config === "object"
      ? { ...pointer.runtime_config }
      : null;

  return {
    source: "registry",
    version_id: pointer.version_id || null,
    pointer_path: pointerPath,
    runtime_model_path: runtimeModelPath,
    runtime_config: runtimeConfig,
    behavior_sha256: pointer.behavior_sha256 || null,
    model_sha256: pointer.model_sha256 || null,
    runtime_model_sha256: pointer.runtime_model_sha256 || null,
    validation_stage: String(pointer.validation_stage || "promotion"),
    validation_evidence_path: pointer.validation_evidence_path || null,
  };
}

function futureMotionRuntimeSettings(resolved, fallback) {
  const config = resolved?.runtime_config;
  const hasFutureConfig =
    config &&
    Object.prototype.hasOwnProperty.call(config, "minimum_confidence");

  if (!hasFutureConfig) {
    return {
      enabled: Boolean(fallback.enabled),
      minimumConfidence: Number(fallback.minimumConfidence),
      minimumBallDistance: Number(fallback.minimumBallDistance),
      allowedRoles: Array.isArray(fallback.allowedRoles)
        ? fallback.allowedRoles.map((role) => String(role).toLowerCase())
        : null,
      source: "plugin_variables",
    };
  }

  const confidence = Number(config.minimum_confidence);
  const distance = Number(config.minimum_ball_distance);
  if (!Number.isFinite(confidence) || confidence < 0 || confidence > 1) {
    throw new Error(
      "invalid champion future minimum_confidence: " +
        String(config.minimum_confidence),
    );
  }
  if (!Number.isFinite(distance) || distance < 0) {
    throw new Error(
      "invalid champion future minimum_ball_distance: " +
        String(config.minimum_ball_distance),
    );
  }

  const configuredRoles = Array.isArray(config.allowed_roles)
    ? config.allowed_roles.map((role) => String(role).trim().toLowerCase())
    : null;
  const validRoles = new Set(["gk", "dm", "am", "st"]);
  const allowedRoles = configuredRoles
    ? configuredRoles.filter(
        (role, index, rows) =>
          validRoles.has(role) && rows.indexOf(role) === index,
      )
    : null;
  if (configuredRoles && !allowedRoles.length) {
    throw new Error("champion future allowed_roles contains no valid roles");
  }

  return {
    enabled: true,
    minimumConfidence: confidence,
    minimumBallDistance: distance,
    allowedRoles,
    source: "champion_registry",
  };
}

module.exports = {
  POINTER_SCHEMA,
  VALIDATION_STAGE_ORDER,
  validationStageRank,
  resolveEliteChampionConfig,
  futureMotionRuntimeSettings,
};

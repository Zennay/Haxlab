"use strict";

const fs = require("fs");
const os = require("os");
const path = require("path");
const initAPI = require("node-haxball");

const API = initAPI();
const { Room, Utils } = API;
const EliteBotPlugin = require("./elite_bot_plugin");
const { scriptedAction } = require("./elite_sandbox_benchmark");
const {
  prepareReplayScenario,
} = require("./sandbox_replay_start");
const {
  discOf,
  statePlayers,
} = require("./elite_features");

const ROLES = ["gk", "dm", "am", "st"];

function usage() {
  console.error(
    "Usage: node tools/elite_plugin_runtime_validation.js " +
      "--pointer current.json --fallback-model-dir DIR " +
      "--stadium stadium.hbs --scenarios scenarios.json " +
      "[--max-scenarios 4] [--seconds 30] [--sample-every 6] " +
      "[--output result.json]",
  );
  process.exit(2);
}

function parseArgs(argv) {
  const result = {
    maxScenarios: 4,
    seconds: 30,
    sampleEvery: 6,
    output: null,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const value = argv[i + 1];
    if (key === "--pointer") { result.pointer = value; i += 1; }
    else if (key === "--fallback-model-dir") {
      result.fallbackModelDir = value; i += 1;
    }
    else if (key === "--stadium") { result.stadium = value; i += 1; }
    else if (key === "--scenarios") { result.scenarios = value; i += 1; }
    else if (key === "--max-scenarios") {
      result.maxScenarios = Number(value); i += 1;
    }
    else if (key === "--seconds") {
      result.seconds = Number(value); i += 1;
    }
    else if (key === "--sample-every") {
      result.sampleEvery = Number(value); i += 1;
    }
    else if (key === "--output") { result.output = value; i += 1; }
    else if (key === "--help" || key === "-h") usage();
    else throw new Error("Unknown argument: " + key);
  }

  if (
    !result.pointer ||
    !result.fallbackModelDir ||
    !result.stadium ||
    !result.scenarios
  ) {
    usage();
  }
  result.maxScenarios = Math.max(1, Math.floor(result.maxScenarios));
  result.seconds = Math.max(10, Number(result.seconds) || 30);
  result.sampleEvery = Math.max(1, Math.floor(result.sampleEvery));
  return result;
}

function writeJson(filePath, payload) {
  fs.writeFileSync(filePath, JSON.stringify(payload, null, 2) + "\n");
}

function clonePointer(sourcePath) {
  const source = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "haxlab-plugin-runtime-"));
  const target = path.join(dir, "current.json");
  writeJson(target, source);
  return { source, dir, target };
}

function makeRoomAdapter(sandbox, inputCounts) {
  return {
    get state() { return sandbox.state; },
    get gameState() { return sandbox.gameState; },
    get gameStateExt() { return sandbox.gameStateExt || null; },

    extrapolate() {
      return sandbox.extrapolate?.();
    },
    fakePlayerJoin(id, name, flag, avatar, conn, auth) {
      return sandbox.playerJoin(id, name, flag, avatar, conn, auth);
    },
    fakePlayerLeave(id) {
      const player = sandbox.state.getPlayer(id);
      if (!player) return null;
      sandbox.playerLeave(id);
      return player;
    },
    fakeSetPlayerTeam(playerId, teamId, byId) {
      return sandbox.setPlayerTeam(playerId, teamId, byId);
    },
    fakeSendPlayerInput(input, byId) {
      inputCounts.set(byId, (inputCounts.get(byId) || 0) + 1);
      return sandbox.playerInput(input, byId);
    },
  };
}

function addBaselinePlayer(sandbox, id, role, teamId) {
  sandbox.playerJoin(
    id,
    "Runtime-" + role.toUpperCase(),
    "xx",
    "AI",
    "runtime-" + id,
    "runtime-" + id,
  );
  sandbox.setPlayerTeam(id, teamId, 0);
}

function createPluginRoom({
  pointerPath,
  fallbackModelDir,
  stadium,
  eliteTeamId,
  sampleEvery,
}) {
  let plugin = null;
  let gameStarts = 0;
  const inputCounts = new Map();

  const sandbox = Room.sandbox(
    {
      onGameStart: () => {
        gameStarts += 1;
        plugin?.onGameStart?.();
      },
      onGameTick: () => {
        plugin?.onGameTick?.();
      },
    },
    { controlledPlayerId: 0 },
  );
  sandbox.setSimulationSpeed(0);
  sandbox.setCurrentStadium(stadium, 0);
  sandbox.setScoreLimit(0, 0);
  sandbox.setTimeLimit(0, 0);

  const adapter = makeRoomAdapter(sandbox, inputCounts);
  plugin = new EliteBotPlugin(API);
  plugin.room = adapter;
  plugin.championPointer = pointerPath;
  plugin.modelDir = fallbackModelDir;
  // Runtime validation intentionally exercises a canary-approved champion.
  plugin.minimumChampionValidationStage = "canary";
  plugin.sampleEveryTicks = sampleEvery;
  plugin.teamId = eliteTeamId;
  plugin.autoSpawn = false;
  plugin.enableRecovery = false;
  plugin.initialize();
  plugin.spawnEliteTeam(eliteTeamId);

  const baselineTeamId = eliteTeamId === 1 ? 2 : 1;
  const baselineBots = ROLES.map((role, index) => {
    const bot = {
      id: 200 + index,
      role,
      teamId: baselineTeamId,
      isElite: false,
    };
    addBaselinePlayer(sandbox, bot.id, role, baselineTeamId);
    return bot;
  });

  const eliteBots = plugin.getEliteRuntimeStatus().bots.map((bot) => ({
    id: bot.id,
    role: bot.role,
    teamId: eliteTeamId,
    isElite: true,
  }));

  return {
    sandbox,
    plugin,
    inputCounts,
    gameStarts: () => gameStarts,
    eliteBots,
    baselineBots,
  };
}

function driveBaseline(room, bots, profile, stadium) {
  const state = room.state;
  const gameState = room.gameState;
  const ball = gameState?.physicsState?.discs?.[0];
  if (!state || !gameState || !ball?.pos) return;

  const players = statePlayers(state);
  for (const bot of bots) {
    const player = state.getPlayer?.(bot.id) ||
      players.find((candidate) => Number(candidate.id) === bot.id);
    if (!player || !discOf(player)?.pos) continue;
    const action = scriptedAction(
      bot.role,
      player,
      ball,
      bot.teamId,
      profile,
      stadium,
    );
    room.playerInput(
      Utils.keyState(action.dirX, action.dirY, action.kick),
      bot.id,
    );
  }
}

function runCase({
  pointerPath,
  fallbackModelDir,
  stadium,
  scenario,
  scenarioIndex,
  eliteTeamId,
  seconds,
  sampleEvery,
}) {
  const context = createPluginRoom({
    pointerPath,
    fallbackModelDir,
    stadium,
    eliteTeamId,
    sampleEvery,
  });
  const {
    sandbox,
    plugin,
    inputCounts,
    eliteBots,
    baselineBots,
  } = context;
  const allBots = [...eliteBots, ...baselineBots];

  try {
    sandbox.startGame(0);
    sandbox.runSteps(5);
    prepareReplayScenario(
      sandbox,
      allBots,
      scenario,
      eliteTeamId,
    );

    const totalTicks = Math.floor(seconds * 60);
    const profile = ["balanced", "compact", "press"][
      (scenarioIndex - 1) % 3
    ];

    for (let tick = 0; tick < totalTicks; tick += 1) {
      if (tick % sampleEvery === 0) {
        driveBaseline(sandbox, baselineBots, profile, stadium);
      }
      sandbox.runSteps(1);
    }

    const status = plugin.getEliteRuntimeStatus();
    const botRows = status.bots.map((bot) => ({
      ...bot,
      adapter_inputs_sent: inputCounts.get(bot.id) || 0,
    }));

    return {
      scenario_index: scenarioIndex,
      source_frame: scenario.frame,
      elite_team_id: eliteTeamId,
      policy: status.policy,
      future_motion: status.future_motion,
      runtime_errors: status.runtime_errors,
      game_starts: context.gameStarts(),
      bots: botRows,
    };
  } finally {
    try { sandbox.stopGame(0); } catch (_) {}
    try { plugin.finalize(); } catch (_) {}
    try { sandbox.destroy(); } catch (_) {}
  }
}

function runBoundaryProbe({
  pointerPath,
  originalPointer,
  fallbackModelDir,
  stadium,
  sampleEvery,
}) {
  const context = createPluginRoom({
    pointerPath,
    fallbackModelDir,
    stadium,
    eliteTeamId: 1,
    sampleEvery,
  });
  const { sandbox, plugin } = context;

  try {
    sandbox.startGame(0);
    sandbox.runSteps(12);
    const initial = plugin.getEliteRuntimeStatus();

    const probeVersion =
      String(originalPointer.version_id) + "-boundary-probe";
    writeJson(pointerPath, {
      ...originalPointer,
      version_id: probeVersion,
    });
    sandbox.runSteps(18);
    const midGame = plugin.getEliteRuntimeStatus();

    sandbox.stopGame(0);
    sandbox.runSteps(2);
    sandbox.startGame(0);
    sandbox.runSteps(8);
    const nextGame = plugin.getEliteRuntimeStatus();

    writeJson(pointerPath, originalPointer);
    sandbox.stopGame(0);
    sandbox.runSteps(2);
    sandbox.startGame(0);
    sandbox.runSteps(8);
    const restored = plugin.getEliteRuntimeStatus();

    return {
      original_version: originalPointer.version_id,
      probe_version: probeVersion,
      initial_version: initial.policy?.version_id || null,
      mid_game_version: midGame.policy?.version_id || null,
      next_game_version: nextGame.policy?.version_id || null,
      restored_version: restored.policy?.version_id || null,
      no_mid_game_reload:
        midGame.policy?.version_id === originalPointer.version_id,
      reloads_on_next_game:
        nextGame.policy?.version_id === probeVersion,
      restores_on_following_game:
        restored.policy?.version_id === originalPointer.version_id,
    };
  } finally {
    writeJson(pointerPath, originalPointer);
    try { sandbox.stopGame(0); } catch (_) {}
    try { plugin.finalize(); } catch (_) {}
    try { sandbox.destroy(); } catch (_) {}
  }
}

function summarize(cases, boundary, pointer) {
  const roles = new Set(ROLES);
  const allBots = cases.flatMap((row) => row.bots);
  const botRoleSet = new Set(allBots.map((bot) => bot.role));
  const roleTotals = Object.fromEntries(
    ROLES.map((role) => [
      role,
      {
        policy_decisions: 0,
        inputs_sent: 0,
        future_assists: 0,
      },
    ]),
  );

  let runtimeErrors = 0;
  for (const row of cases) {
    runtimeErrors += Number(row.runtime_errors || 0);
    for (const bot of row.bots) {
      const target = roleTotals[bot.role];
      if (!target) continue;
      target.policy_decisions += Number(bot.policy_decisions || 0);
      target.inputs_sent += Number(
        bot.adapter_inputs_sent ?? bot.inputs_sent ?? 0,
      );
      target.future_assists += Number(bot.future_assists || 0);
    }
  }

  const totalDecisions = Object.values(roleTotals).reduce(
    (sum, row) => sum + row.policy_decisions,
    0,
  );
  const totalInputs = Object.values(roleTotals).reduce(
    (sum, row) => sum + row.inputs_sent,
    0,
  );
  const totalAssists = Object.values(roleTotals).reduce(
    (sum, row) => sum + row.future_assists,
    0,
  );
  const allowedRoles = new Set(
    (pointer.runtime_config?.allowed_roles || []).map(
      (role) => String(role).toLowerCase(),
    ),
  );
  const forbiddenAssists = Object.entries(roleTotals)
    .filter(([role]) => !allowedRoles.has(role))
    .reduce((sum, [, row]) => sum + row.future_assists, 0);

  const first = cases[0] || {};
  const expectedVersion = pointer.version_id;
  const expectedStage = String(pointer.validation_stage || "promotion");
  const expectedConfidence = Number(
    pointer.runtime_config?.minimum_confidence,
  );
  const expectedDistance = Number(
    pointer.runtime_config?.minimum_ball_distance,
  );

  const checks = {
    enough_cases: cases.length >= 8,
    registry_source_loaded:
      cases.every((row) => row.policy?.source === "registry"),
    correct_champion_version:
      cases.every((row) => row.policy?.version_id === expectedVersion),
    canary_or_higher_loaded:
      ["canary", "runtime", "live"].includes(expectedStage) &&
      cases.every((row) =>
        ["canary", "runtime", "live"].includes(
          String(row.policy?.validation_stage || ""),
        ),
      ),
    exact_role_set:
      roles.size === botRoleSet.size &&
      [...roles].every((role) => botRoleSet.has(role)),
    all_roles_make_policy_decisions:
      ROLES.every((role) => roleTotals[role].policy_decisions > 0),
    all_roles_emit_inputs:
      ROLES.every((role) => roleTotals[role].inputs_sent > 0),
    zero_runtime_errors: runtimeErrors === 0,
    registry_future_config_loaded:
      first.future_motion?.source === "champion_registry" &&
      first.future_motion?.enabled === true &&
      Number(first.future_motion?.minimum_confidence) === expectedConfidence &&
      Number(first.future_motion?.minimum_ball_distance) === expectedDistance,
    exact_future_role_gate:
      JSON.stringify(
        [...new Set(first.future_motion?.allowed_roles || [])].sort(),
      ) === JSON.stringify([...allowedRoles].sort()),
    future_assist_exercised: totalAssists > 0,
    no_forbidden_role_assists: forbiddenAssists === 0,
    no_mid_game_hot_reload: boundary.no_mid_game_reload === true,
    reloads_at_game_boundary: boundary.reloads_on_next_game === true,
    restores_at_game_boundary:
      boundary.restores_on_following_game === true,
  };

  return {
    schema: "haxlab-elite-plugin-runtime-validation-v1",
    validated: Object.values(checks).every(Boolean),
    candidate: {
      version_id: expectedVersion,
      validation_stage_before_runtime: expectedStage,
      runtime_model_path: pointer.runtime_model_path || null,
      runtime_config: pointer.runtime_config || null,
    },
    aggregate: {
      case_count: cases.length,
      policy_decisions: totalDecisions,
      inputs_sent: totalInputs,
      future_assists: totalAssists,
      forbidden_role_future_assists: forbiddenAssists,
      runtime_errors: runtimeErrors,
      roles: roleTotals,
    },
    boundary_reload: boundary,
    checks,
    cases,
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const pointerCopy = clonePointer(args.pointer);
  const stadiumJson = JSON.parse(fs.readFileSync(args.stadium, "utf8"));
  const stadium = Utils.parseStadium(JSON.stringify(stadiumJson));
  const scenarioPayload = JSON.parse(
    fs.readFileSync(args.scenarios, "utf8"),
  );
  const scenarios = (scenarioPayload.scenarios || []).slice(
    0,
    args.maxScenarios,
  );
  if (!scenarios.length) throw new Error("scenario file contains no scenarios");

  try {
    const boundary = runBoundaryProbe({
      pointerPath: pointerCopy.target,
      originalPointer: pointerCopy.source,
      fallbackModelDir: args.fallbackModelDir,
      stadium,
      sampleEvery: args.sampleEvery,
    });

    const cases = [];
    for (let index = 0; index < scenarios.length; index += 1) {
      for (const eliteTeamId of [1, 2]) {
        cases.push(
          runCase({
            pointerPath: pointerCopy.target,
            fallbackModelDir: args.fallbackModelDir,
            stadium,
            scenario: scenarios[index],
            scenarioIndex: index + 1,
            eliteTeamId,
            seconds: args.seconds,
            sampleEvery: args.sampleEvery,
          }),
        );
      }
    }

    const result = summarize(cases, boundary, pointerCopy.source);
    const rendered = JSON.stringify(result, null, 2) + "\n";
    if (args.output) fs.writeFileSync(args.output, rendered);
    process.stdout.write(rendered);
    if (!result.validated) process.exitCode = 1;
  } finally {
    fs.rmSync(pointerCopy.dir, { recursive: true, force: true });
  }
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.error(error?.stack || String(error));
    process.exit(1);
  }
}

module.exports = {
  makeRoomAdapter,
  summarize,
};

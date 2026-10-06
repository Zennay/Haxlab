"use strict";

const assert = require("assert");
const {
  applyReplayScenario,
  primeElitePolicy,
} = require("../tools/sandbox_replay_start");
const {
  resetNeutral4v4,
  releaseInitialKickoff,
} = require("../tools/sandbox_neutral_start");

const roles = ["gk", "dm", "am", "st"];

function lineup({ eliteTeamId = 1 } = {}) {
  const baselineTeamId = eliteTeamId === 1 ? 2 : 1;
  return [
    ...roles.map((role, index) => ({
      id: index + 1,
      teamId: eliteTeamId,
      role,
      isElite: true,
    })),
    ...roles.map((role, index) => ({
      id: index + 5,
      teamId: baselineTeamId,
      role,
      isElite: false,
    })),
  ];
}

function neutralLineup() {
  return [
    ...roles.map((role, index) => ({
      id: index + 1,
      teamId: 1,
      role,
    })),
    ...roles.map((role, index) => ({
      id: index + 5,
      teamId: 2,
      role,
    })),
  ];
}

const room = {};
const scenario = {
  teams: { "1": {}, "2": {} },
  ball: { x: 0, y: 0, vx: 0, vy: 0 },
};

assert.throws(
  () => applyReplayScenario(room, lineup().slice(0, 7), scenario, 1),
  /requires exactly 8 bots/,
);

const duplicateId = lineup();
duplicateId[4].id = duplicateId[0].id;
assert.throws(
  () => applyReplayScenario(room, duplicateId, scenario, 1),
  /bot ids must be unique/,
);

const coercedTeam = lineup();
coercedTeam[0].teamId = "1";
assert.throws(
  () => applyReplayScenario(room, coercedTeam, scenario, 1),
  /teamId must be native team id 1 or 2/,
);

const coercedElite = lineup();
coercedElite[0].isElite = 1;
assert.throws(
  () => applyReplayScenario(room, coercedElite, scenario, 1),
  /isElite must be a native boolean/,
);

const duplicateRole = lineup();
duplicateRole[1].role = "gk";
assert.throws(
  () => applyReplayScenario(room, duplicateRole, scenario, 1),
  /one bot per cohort\/role slot/,
);

const wrongCohortTeam = lineup();
wrongCohortTeam[0].teamId = 2;
assert.throws(
  () => applyReplayScenario(room, wrongCohortTeam, scenario, 1),
  /scenario bot team mismatch/,
);

const neutral = neutralLineup();
assert.throws(
  () => resetNeutral4v4(room, neutral, "0"),
  /pairIndex must be a non-negative native integer/,
);
assert.throws(
  () => resetNeutral4v4(room, neutral, -1),
  /pairIndex must be a non-negative native integer/,
);

const neutralDuplicateRole = neutralLineup();
neutralDuplicateRole[1].role = "gk";
assert.throws(
  () => releaseInitialKickoff(room, neutralDuplicateRole),
  /one bot per team\/role slot/,
);

const eliteBots = lineup().filter((bot) => bot.isElite);
const policy = { reset() {}, act() {} };

assert.throws(
  () => primeElitePolicy(policy, eliteBots, { history: {} }),
  /scenario history must be an array/,
);
assert.throws(
  () => primeElitePolicy(policy, eliteBots, { history: [null] }),
  /scenario history frame must be an object/,
);
assert.throws(
  () => primeElitePolicy(policy, eliteBots.slice(0, 3), { history: [] }),
  /requires exactly 4 elite bots/,
);
assert.throws(
  () => primeElitePolicy(
    policy,
    eliteBots,
    { history: [{ features: { "1": { gk: [] } } }] },
  ),
  /scenario history missing elite warmup features/,
);

console.log("test_sandbox_start_input_integrity: ok");

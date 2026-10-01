"use strict";

const fs = require("fs");
const path = require("path");
const initAPI = require("node-haxball");
const { scriptedAction } = require("./elite_sandbox_benchmark");
const { discOf, statePlayers, num } = require("./elite_features");
const { prepareNeutralStart } = require("./sandbox_neutral_start");

const API = initAPI();
const { Room, Utils } = API;
const ROLES = ["gk", "dm", "am", "st"];
const PROFILES = ["balanced", "compact", "press"];

function addPlayer(room, id, name, teamId) {
  room.playerJoin(id, name, "xx", "AI", "sanity-" + id, "sanity-" + id);
  room.setPlayerTeam(id, teamId, 0);
}

function runSanityMatch(stadium, profile, minutes, sampleEvery, matchIndex) {
  const room = Room.sandbox({}, { controlledPlayerId: 0 });
  room.setSimulationSpeed(0);
  room.setCurrentStadium(stadium, 0);
  room.setScoreLimit(0, 0);
  room.setTimeLimit(0, 0);

  const bots = [];
  for (const teamId of [1, 2]) {
    for (let index = 0; index < 4; index += 1) {
      const role = ROLES[index];
      const id = teamId * 100 + index;
      addPlayer(room, id, "S" + teamId + "-" + role, teamId);
      bots.push({ id, role, teamId });
    }
  }

  room.startGame(0);
  room.runSteps(5);
  prepareNeutralStart(room, bots, matchIndex - 1);

  const totalTicks = Math.floor(minutes * 60 * 60);
  let redHalf = 0;
  let blueHalf = 0;
  let neutral = 0;
  let samples = 0;
  let redAttackThird = 0;
  let blueAttackThird = 0;

  for (let tick = 0; tick < totalTicks; tick += 1) {
    if (tick % sampleEvery === 0) {
      const state = room.state;
      const gameState = room.gameState;
      const ball = gameState?.physicsState?.discs?.[0];
      if (state && gameState && ball?.pos) {
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

        samples += 1;
        const x = num(ball.pos.x);
        if (x > 10) redHalf += 1;
        else if (x < -10) blueHalf += 1;
        else neutral += 1;

        const attackThird = Math.max(110, Number(stadium.width || 420) * 0.34);
        if (x > attackThird) redAttackThird += 1;
        else if (x < -attackThird) blueAttackThird += 1;
      }
    }
    room.runSteps(1);
  }

  try { room.stopGame(0); } catch (_) {}
  try { room.destroy(); } catch (_) {}

  const denom = Math.max(1, samples);
  return {
    match_index: matchIndex,
    profile,
    red_half_rate: redHalf / denom,
    blue_half_rate: blueHalf / denom,
    neutral_rate: neutral / denom,
    red_attack_third_rate: redAttackThird / denom,
    blue_attack_third_rate: blueAttackThird / denom,
    side_bias_abs: Math.abs(redHalf - blueHalf) / denom,
  };
}

function main() {
  const args = process.argv.slice(2);
  const stadiumIndex = args.indexOf("--stadium");
  const outputIndex = args.indexOf("--output");
  const matchesIndex = args.indexOf("--matches");
  const minutesIndex = args.indexOf("--minutes");
  const sampleIndex = args.indexOf("--sample-every");

  if (stadiumIndex < 0 || !args[stadiumIndex + 1]) {
    throw new Error("--stadium is required");
  }

  const stadiumPath = args[stadiumIndex + 1];
  const outputPath = outputIndex >= 0 ? args[outputIndex + 1] : null;
  const matches = matchesIndex >= 0 ? Math.max(3, Number(args[matchesIndex + 1])) : 6;
  const minutes = minutesIndex >= 0 ? Math.max(0.25, Number(args[minutesIndex + 1])) : 1.0;
  const sampleEvery = sampleIndex >= 0 ? Math.max(1, Number(args[sampleIndex + 1])) : 6;

  const stadiumJson = JSON.parse(fs.readFileSync(stadiumPath, "utf8"));
  const stadium = Utils.parseStadium(JSON.stringify(stadiumJson));

  const rows = [];
  for (let index = 0; index < matches; index += 1) {
    const profile = PROFILES[index % PROFILES.length];
    rows.push(runSanityMatch(stadium, profile, minutes, sampleEvery, index + 1));
  }

  const avg = (key) =>
    rows.reduce((sum, row) => sum + Number(row[key] || 0), 0) /
    Math.max(1, rows.length);

  const averageRedHalfRate = avg("red_half_rate");
  const averageBlueHalfRate = avg("blue_half_rate");
  const result = {
    schema: "haxlab-sandbox-side-sanity-v1",
    stadium: {
      name: stadium.name || null,
      width: stadium.width ?? null,
      height: stadium.height ?? null,
    },
    matches: rows.length,
    average_red_half_rate: averageRedHalfRate,
    average_blue_half_rate: averageBlueHalfRate,
    aggregate_side_bias_abs: Math.abs(
      averageRedHalfRate - averageBlueHalfRate,
    ),
    average_match_side_extremity: avg("side_bias_abs"),
    average_red_attack_third_rate: avg("red_attack_third_rate"),
    average_blue_attack_third_rate: avg("blue_attack_third_rate"),
    match_results: rows,
  };

  const rendered = JSON.stringify(result, null, 2) + "\n";
  if (outputPath) {
    fs.mkdirSync(path.dirname(outputPath), { recursive: true });
    fs.writeFileSync(outputPath, rendered);
  }
  process.stdout.write(rendered);
}

if (require.main === module) {
  try { main(); }
  catch (error) {
    console.error(error?.stack || String(error));
    process.exit(1);
  }
}

module.exports = { runSanityMatch };

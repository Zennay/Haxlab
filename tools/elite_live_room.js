"use strict";

const fs = require("fs");
const path = require("path");
const EliteBotPlugin = require("./elite_bot_plugin");
const {
  resolveEliteChampionConfig,
} = require("./elite_champion_config");

const DEFAULT_POINTER =
  "/var/lib/haxlab/derived/champions/elite-player/live.json";
const DEFAULT_MODEL_DIR =
  "/var/lib/haxlab/derived/training/elite-player-v01/model";

function parseBoolean(value, fallback = false) {
  if (value == null || value === "") return fallback;
  return ["1", "true", "yes", "on"].includes(
    String(value).trim().toLowerCase(),
  );
}

function parseTeam(value) {
  const normalized = String(value || "red").trim().toLowerCase();
  if (normalized === "red" || normalized === "1") return 1;
  if (normalized === "blue" || normalized === "2") return 2;
  throw new Error(`invalid bot team: ${value}`);
}

function parseArgs(argv = process.argv.slice(2), env = process.env) {
  const options = {
    roomName: env.HAXLAB_LIVE_ROOM_NAME || "HaxLab Live Champion Test",
    password: env.HAXLAB_LIVE_ROOM_PASSWORD || null,
    showInRoomList: parseBoolean(env.HAXLAB_LIVE_PUBLIC, false),
    maxPlayerCount: Number(env.HAXLAB_LIVE_MAX_PLAYERS || 12),
    botTeam: parseTeam(env.HAXLAB_LIVE_BOT_TEAM || "red"),
    scoreLimit: Number(env.HAXLAB_LIVE_SCORE_LIMIT || 3),
    timeLimit: Number(env.HAXLAB_LIVE_TIME_LIMIT || 5),
    autoStart: parseBoolean(env.HAXLAB_LIVE_AUTO_START, true),
    stadiumPath: env.HAXLAB_LIVE_STADIUM || null,
    pointerPath: env.HAXLAB_ELITE_CHAMPION_POINTER || DEFAULT_POINTER,
    modelDir: env.HAXLAB_ELITE_MODEL_DIR || DEFAULT_MODEL_DIR,
    dryRun: false,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i];
    const next = () => {
      i += 1;
      if (i >= argv.length) throw new Error(`missing value for ${key}`);
      return argv[i];
    };
    if (key === "--name") options.roomName = next();
    else if (key === "--password") options.password = next();
    else if (key === "--public") options.showInRoomList = true;
    else if (key === "--private") options.showInRoomList = false;
    else if (key === "--team") options.botTeam = parseTeam(next());
    else if (key === "--stadium") options.stadiumPath = next();
    else if (key === "--score-limit") options.scoreLimit = Number(next());
    else if (key === "--time-limit") options.timeLimit = Number(next());
    else if (key === "--max-players") options.maxPlayerCount = Number(next());
    else if (key === "--no-auto-start") options.autoStart = false;
    else if (key === "--dry-run") options.dryRun = true;
    else throw new Error(`unknown argument: ${key}`);
  }
  options.humanTeam = options.botTeam === 1 ? 2 : 1;
  return options;
}

function resolveRoomToken(env = process.env) {
  const direct = String(env.HAXBALL_HEADLESS_TOKEN || "").trim();
  if (direct) return direct;
  const filePath = String(env.HAXBALL_HEADLESS_TOKEN_FILE || "").trim();
  if (filePath) {
    const token = fs.readFileSync(filePath, "utf8").trim();
    if (token) return token;
  }
  throw new Error(
    "missing HaxBall token: set HAXBALL_HEADLESS_TOKEN or HAXBALL_HEADLESS_TOKEN_FILE",
  );
}

function resolveLiveChampion(options) {
  const resolved = resolveEliteChampionConfig({
    pointerPath: options.pointerPath,
    fallbackModelDir: options.modelDir,
    minimumValidationStage: "live",
  });
  if (resolved.source !== "registry" || resolved.validation_stage !== "live") {
    throw new Error("live room requires an explicitly activated live champion");
  }
  return resolved;
}

function buildStadium(API, options) {
  if (!options.stadiumPath) {
    const stadium = API.Utils.getDefaultStadiums().find(
      (item) => item.name === "Big",
    );
    if (!stadium) throw new Error("default Big stadium is unavailable");
    return { stadium, source: "default:Big", canonical: false };
  }
  const absolute = path.resolve(options.stadiumPath);
  const raw = fs.readFileSync(absolute, "utf8");
  return {
    stadium: API.Utils.parseStadium(raw),
    source: absolute,
    canonical: true,
  };
}

function isEliteBot(player) {
  return (
    Number(player?.id || 0) >= 64000 ||
    String(player?.auth || "").startsWith("haxlab-elite-")
  );
}

function summarizeStatus(plugin) {
  const status = plugin.getEliteRuntimeStatus?.() || {};
  return {
    schema: "haxlab-live-room-status-v1",
    at: new Date().toISOString(),
    policy: status.policy || null,
    runtime_errors: Number(status.runtime_errors || 0),
    bots: Array.isArray(status.bots) ? status.bots : [],
  };
}

function createLiveRoom(options, env = process.env) {
  const champion = resolveLiveChampion(options);
  if (options.dryRun) return { champion, room: null, plugin: null };

  const API = require("node-haxball")();
  const token = resolveRoomToken(env);
  const plugin = new EliteBotPlugin(API);
  plugin.autoSpawn = true;
  plugin.teamId = options.botTeam;
  plugin.championPointer = options.pointerPath;
  plugin.modelDir = options.modelDir;
  plugin.minimumChampionValidationStage = "live";

  let statusTimer = null;
  let openedRoom = null;
  API.Room.create({
    name: options.roomName,
    password: options.password || null,
    showInRoomList: options.showInRoomList,
    maxPlayerCount: options.maxPlayerCount,
    token,
  }, {
    storage: { player_name: "HaxLab" },
    libraries: [],
    config: null,
    renderer: null,
    plugins: [plugin],

    onOpen: (room) => {
      openedRoom = room;
      const stadiumInfo = buildStadium(API, options);
      room.setCurrentStadium(stadiumInfo.stadium);
      room.setScoreLimit(options.scoreLimit);
      room.setTimeLimit(options.timeLimit);

      console.log("HAXLAB_LIVE_ROOM_OPEN", JSON.stringify({
        champion: champion.version_id,
        validation_stage: champion.validation_stage,
        bot_team: options.botTeam,
        human_team: options.humanTeam,
        stadium: stadiumInfo.source,
        canonical_stadium: stadiumInfo.canonical,
      }));
      if (!stadiumInfo.canonical) {
        console.warn(
          "HAXLAB_LIVE_WARNING using default Big; pass --stadium for the validated custom stadium",
        );
      }

      room.onAfterRoomLink = (link) => {
        console.log("HAXLAB_LIVE_ROOM_LINK", link);
      };

      room.onPlayerJoin = (player) => {
        if (isEliteBot(player)) return;
        try {
          room.setPlayerAdmin(player.id, true);
          room.setPlayerTeam(player.id, options.humanTeam);
          console.log("HAXLAB_LIVE_HUMAN_JOIN", JSON.stringify({
            id: player.id,
            name: player.name,
            team: options.humanTeam,
          }));
          if (options.autoStart && !room.gameState) {
            setTimeout(() => {
              try {
                if (!room.gameState) room.startGame();
              } catch (error) {
                console.error("HAXLAB_LIVE_AUTOSTART_ERROR", String(error));
              }
            }, 250);
          }
        } catch (error) {
          console.error("HAXLAB_LIVE_JOIN_SETUP_ERROR", String(error));
        }
      };

      statusTimer = setInterval(() => {
        console.log("HAXLAB_LIVE_STATUS", JSON.stringify(summarizeStatus(plugin)));
      }, 5000);
    },

    onClose: (message) => {
      if (statusTimer) clearInterval(statusTimer);
      console.log("HAXLAB_LIVE_ROOM_CLOSED", String(message || ""));
    },
  });

  return { champion, room: openedRoom, plugin };
}

function main() {
  try {
    const options = parseArgs();
    const result = createLiveRoom(options);
    if (options.dryRun) {
      console.log("HAXLAB_LIVE_DRY_RUN_OK", JSON.stringify({
        champion: result.champion.version_id,
        validation_stage: result.champion.validation_stage,
        pointer: options.pointerPath,
        stadium: options.stadiumPath || "default:Big",
      }));
    }
  } catch (error) {
    console.error("HAXLAB_LIVE_ERROR", error?.stack || String(error));
    process.exitCode = 1;
  }
}

if (require.main === module) main();

module.exports = {
  parseBoolean,
  parseTeam,
  parseArgs,
  resolveRoomToken,
  resolveLiveChampion,
  isEliteBot,
  summarizeStatus,
  createLiveRoom,
};

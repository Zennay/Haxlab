"use strict";

function stringSet(value) {
  return new Set(
    (Array.isArray(value) ? value : [value])
      .filter((item) => item != null)
      .map((item) => String(item)),
  );
}

function replayPlayerDiscTeam(disc) {
  if (!disc || typeof disc !== "object") return null;

  const groups = stringSet(disc.cGroup);
  const masks = stringSet(disc.cMask);
  const team =
    groups.size === 1 && groups.has("red")
      ? "red"
      : groups.size === 1 && groups.has("blue")
        ? "blue"
        : null;
  if (!team) return null;

  const requiredMask = ["red", "blue", "ball", "wall"];
  if (!requiredMask.every((flag) => masks.has(flag))) return null;

  const radius = Number(disc.radius);
  const invMass = Number(disc.invMass);
  const color = String(disc.color ?? "").toLowerCase();
  if (!(radius >= 14 && radius <= 16)) return null;
  if (!(invMass > 0 && invMass <= 1)) return null;
  if (!["0", "000000", "black"].includes(color)) return null;

  return team;
}

function sanitizeReplayPlayerDiscs(stadium) {
  if (!stadium || typeof stadium !== "object") {
    return { stadium, removed: 0, detected: false };
  }

  const discs = Array.isArray(stadium.discs) ? stadium.discs : [];
  const candidates = [];
  let red = 0;
  let blue = 0;

  discs.forEach((disc, index) => {
    const team = replayPlayerDiscTeam(disc);
    if (!team) return;
    candidates.push(index);
    if (team === "red") red += 1;
    else blue += 1;
  });

  // HaxLab's canonical dataset is 4v4. Only strip the exact symmetric
  // replay-player signature; do not remove arbitrary custom-map physics.
  const detected = candidates.length === 8 && red === 4 && blue === 4;
  if (!detected) return { stadium, removed: 0, detected: false };

  const remove = new Set(candidates);
  return {
    stadium: {
      ...stadium,
      discs: discs.filter((_, index) => !remove.has(index)),
    },
    removed: candidates.length,
    detected: true,
  };
}

module.exports = {
  replayPlayerDiscTeam,
  sanitizeReplayPlayerDiscs,
};

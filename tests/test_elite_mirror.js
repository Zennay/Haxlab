"use strict";

const assert = require("assert");
const {
  buildFeatureObject,
  canonicalActionToWorld,
} = require("../tools/elite_features");

function disc(x, y, vx=0, vy=0) {
  return { pos:{x,y}, speed:{x:vx,y:vy} };
}
function player(id, teamId, x, y, vx=0, vy=0) {
  return { id, team:{id:teamId}, disc:disc(x,y,vx,vy) };
}
function state(players) {
  return {
    players,
    getPlayer(id) { return this.players.find(p => p.id === id); },
  };
}
function mirrorPlayer(p) {
  return player(
    p.id + 100,
    p.team.id === 1 ? 2 : 1,
    -p.disc.pos.x,
    p.disc.pos.y,
    -p.disc.speed.x,
    p.disc.speed.y,
  );
}

const redPlayers=[
  player(1,1,-120,-20,1,0),
  player(2,1,-40,10,0.5,0.1),
  player(3,1,40,-5,0.2,-0.1),
  player(4,1,130,15,0,0),
  player(5,2,130,-25,-0.2,0),
  player(6,2,45,5,-0.3,0.1),
  player(7,2,-35,-10,-0.1,0),
  player(8,2,-125,20,0,0),
];
const redGame={
  physicsState:{discs:[disc(20,12,1.5,-0.3)]},
  scores:{red:2,blue:1},
};
const red=buildFeatureObject(redPlayers[1],state(redPlayers),redGame);

const bluePlayers=redPlayers.map(mirrorPlayer);
const blueGame={
  physicsState:{discs:[disc(-20,12,-1.5,-0.3)]},
  scores:{red:1,blue:2},
};
const blueControlled=bluePlayers.find(p => p.id === 102);
const blue=buildFeatureObject(blueControlled,state(bluePlayers),blueGame);

for (const key of Object.keys(red)) {
  assert.ok(
    Math.abs(red[key]-blue[key]) < 1e-9,
    key + ": red=" + red[key] + " blue=" + blue[key],
  );
}

assert.deepStrictEqual(
  canonicalActionToWorld({dir_x:1,dir_y:-1,kick:true},1),
  {dirX:1,dirY:-1,kick:true},
);
assert.deepStrictEqual(
  canonicalActionToWorld({dir_x:1,dir_y:-1,kick:true},2),
  {dirX:-1,dirY:-1,kick:true},
);

console.log("elite mirror invariance: ok");

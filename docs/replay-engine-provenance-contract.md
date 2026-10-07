# Replay engine provenance contract

HaxLab's replay decoder writes the Node replay engine identity into each derived
analysis artifact. That evidence must stay synchronized with the dependency that
the repository declares.

## Contract

- `package.json` must pin `node-haxball` to one exact semantic version, not a
  range, tag, wildcard or floating selector.
- `tools/decode_replay.js` must emit exactly
  `node-haxball@<declared dependency version>` in its `decoder` provenance
  field.
- A dependency upgrade and its provenance update therefore form one atomic
  review change: changing only one side fails the contract test.
- This guard does not choose or upgrade the replay engine. It only prevents
  derived artifacts from claiming a decoder version different from the declared
  runtime dependency.

The contract is intentionally additive and does not alter decoder behavior.

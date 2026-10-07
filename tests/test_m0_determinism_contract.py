import json
import struct
from pathlib import Path

from haxlab.ingestion.pipeline import run_import


ARTIFACTS = (
    "manifest.json",
    "replays.json",
    "duplicates.json",
    "reports.json",
    "matches.jsonl",
)


def _write_source(root: Path, *, reverse: bool) -> None:
    root.mkdir()

    replays = [
        (
            "24-09-26-22h12-alpha.hbr2",
            struct.pack(">4sII", b"HBR2", 3, 600) + b"alpha-payload",
        ),
        (
            "24-09-26-22h13-bravo.hbr2",
            struct.pack(">4sII", b"HBR2", 3, 720) + b"bravo-payload",
        ),
    ]
    if reverse:
        replays.reverse()

    sizes: dict[str, int] = {}
    for name, payload in replays:
        path = root / name
        path.write_bytes(payload)
        sizes[name] = path.stat().st_size

    messages = [
        {
            "id": "message-1",
            "timestamp": "2026-09-24T22:12:27+02:00",
            "content": (
                "MATCH REPORT SCRIM #SCRIM-ALPHA\n"
                "Red Team 3 - 2 Blue Team\n"
                "Possession: 🔴 52.34% 🔵 47.66%"
            ),
            "attachments": [
                {
                    "fileName": "24-09-26-22h12-alpha.hbr2",
                    "fileSizeBytes": sizes["24-09-26-22h12-alpha.hbr2"],
                }
            ],
        },
        {
            "id": "message-2",
            "timestamp": "2026-09-24T22:13:15+02:00",
            "content": (
                "MATCH REPORT SCRIM #SCRIM-BRAVO\n"
                "Red Team 1 - 1 Blue Team\n"
                "Possession: 🔴 49.50% 🔵 50.50%"
            ),
            "attachments": [
                {
                    "fileName": "24-09-26-22h13-bravo.hbr2",
                    "fileSizeBytes": sizes["24-09-26-22h13-bravo.hbr2"],
                }
            ],
        },
    ]
    if reverse:
        messages.reverse()

    payload = {
        "channel": {"id": "726932424172371968"},
        "messages": messages,
    }
    (root / "channel.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_m0_artifacts_are_byte_deterministic_across_source_order(
    tmp_path: Path,
) -> None:
    raw_a = tmp_path / "raw-a"
    raw_b = tmp_path / "raw-b"
    out_a = tmp_path / "out-a"
    out_b = tmp_path / "out-b"

    _write_source(raw_a, reverse=False)
    _write_source(raw_b, reverse=True)

    manifest_a = run_import(raw_a, out_a)
    manifest_b = run_import(raw_b, out_b)

    assert manifest_a.as_dict() == manifest_b.as_dict()
    assert manifest_a.match_count == 2
    assert not manifest_a.failures

    for artifact in ARTIFACTS:
        assert (out_a / artifact).read_bytes() == (out_b / artifact).read_bytes()


def test_m0_rerun_restores_canonical_bytes_after_derived_tamper(
    tmp_path: Path,
) -> None:
    raw = tmp_path / "raw"
    out = tmp_path / "out"
    _write_source(raw, reverse=True)

    run_import(raw, out)
    canonical = {name: (out / name).read_bytes() for name in ARTIFACTS}

    for index, name in enumerate(ARTIFACTS):
        (out / name).write_bytes(f"tampered-{index}\n".encode("utf-8"))

    run_import(raw, out)

    for name, expected in canonical.items():
        assert (out / name).read_bytes() == expected


def test_m0_duplicate_inventory_is_byte_deterministic_across_creation_order(
    tmp_path: Path,
) -> None:
    roots = [tmp_path / "duplicates-a", tmp_path / "duplicates-b"]
    outputs = [tmp_path / "duplicates-out-a", tmp_path / "duplicates-out-b"]
    replay_bytes = struct.pack(">4sII", b"HBR2", 3, 600) + b"same-replay"

    for reverse, root in enumerate(roots):
        root.mkdir()
        (root / "copies").mkdir()
        paths = [root / "canonical.hbr2", root / "copies" / "duplicate.hbr2"]
        if reverse:
            paths.reverse()
        for path in paths:
            path.write_bytes(replay_bytes)
        (root / "channel.json").write_text(
            json.dumps({"channel": {"id": "c"}, "messages": []}),
            encoding="utf-8",
        )

    first = run_import(roots[0], outputs[0])
    second = run_import(roots[1], outputs[1])

    assert first.as_dict() == second.as_dict()
    assert first.replay_count == 2
    assert first.unique_replay_count == 1
    assert first.duplicate_replay_count == 1

    for artifact in ARTIFACTS:
        assert (outputs[0] / artifact).read_bytes() == (
            outputs[1] / artifact
        ).read_bytes()

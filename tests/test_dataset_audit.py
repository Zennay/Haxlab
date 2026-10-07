import json
from pathlib import Path

from haxlab.ingestion.dataset_audit import AUDIT_SCHEMA, audit_dataset, main


SHA = "a" * 64


def _write_dataset(root: Path) -> None:
    root.mkdir()
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "replay_count": 1,
                "unique_replay_count": 1,
                "duplicate_replay_count": 0,
                "report_count": 1,
                "match_count": 1,
                "unmatched_replays": [],
                "unmatched_reports": [],
                "failures": [],
            }
        ),
        encoding="utf-8",
    )
    (root / "replays.json").write_text(
        json.dumps(
            [
                {
                    "sha256": SHA,
                    "source_path": "replays/game.hbr2",
                    "file_name": "game.hbr2",
                    "size_bytes": 123,
                    "basic_validation": {"valid": True, "reasons": []},
                }
            ]
        ),
        encoding="utf-8",
    )
    (root / "duplicates.json").write_text("{}", encoding="utf-8")
    (root / "reports.json").write_text(
        json.dumps([{"message_id": "message-1", "content": "report", "report_id": "scrim-1"}]),
        encoding="utf-8",
    )
    (root / "matches.jsonl").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "match_id": "scrim-1",
                "replay_sha256": SHA,
                "replay_source_path": "replays/game.hbr2",
                "source_message_id": "message-1",
                "match_confidence": 0.95,
                "match_reasons": ["attachment_filename"],
                "report": {
                    "message_id": "message-1",
                    "content": "report",
                    "report_id": "scrim-1",
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )


def test_valid_dataset_passes_cross_artifact_audit(tmp_path: Path) -> None:
    root = tmp_path / "m0"
    _write_dataset(root)

    result = audit_dataset(root)

    assert result.ok is True
    assert result.issues == ()
    assert result.replay_count == 1
    assert result.report_count == 1
    assert result.match_count == 1
    assert result.as_dict()["schema"] == AUDIT_SCHEMA


def test_audit_rejects_count_and_reference_drift(tmp_path: Path) -> None:
    root = tmp_path / "m0"
    _write_dataset(root)

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["match_count"] = 2
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    match = json.loads((root / "matches.jsonl").read_text(encoding="utf-8"))
    match["replay_source_path"] = "replays/other.hbr2"
    match["source_message_id"] = "missing-message"
    match["report"]["message_id"] = "different-message"
    (root / "matches.jsonl").write_text(json.dumps(match) + "\n", encoding="utf-8")

    result = audit_dataset(root)

    assert result.ok is False
    assert "matches:0:replay_source_mismatch" in result.issues
    assert "matches:0:unknown_source_message_id" in result.issues
    assert "manifest:match_count:mismatch" in result.issues
    assert "manifest:unmatched_reports:mismatch" in result.issues


def test_audit_rejects_duplicate_inventory_and_manifest_sets(tmp_path: Path) -> None:
    root = tmp_path / "m0"
    _write_dataset(root)

    replays = json.loads((root / "replays.json").read_text(encoding="utf-8"))
    replays.append(dict(replays[0]))
    (root / "replays.json").write_text(json.dumps(replays), encoding="utf-8")

    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["unmatched_reports"] = ["message-1", "message-1"]
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    result = audit_dataset(root)

    assert result.ok is False
    assert "replays:1:duplicate_sha256" in result.issues
    assert "replays:1:duplicate_source_path" in result.issues
    assert "manifest:unmatched_reports:duplicates" in result.issues


def test_audit_rejects_malformed_jsonl_without_crashing(tmp_path: Path) -> None:
    root = tmp_path / "m0"
    _write_dataset(root)
    (root / "matches.jsonl").write_text("{not-json}\n\n", encoding="utf-8")

    result = audit_dataset(root)

    assert result.ok is False
    assert "matches:invalid_json:1" in result.issues
    assert "matches:blank_line:2" in result.issues
    assert "manifest:match_count:mismatch" in result.issues


def test_audit_rejects_symlinked_required_artifact(tmp_path: Path) -> None:
    root = tmp_path / "m0"
    _write_dataset(root)

    real = root / "reports-real.json"
    (root / "reports.json").replace(real)
    (root / "reports.json").symlink_to(real)

    result = audit_dataset(root)

    assert result.ok is False
    assert "reports:symlink" in result.issues


def test_cli_returns_nonzero_and_machine_readable_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    root = tmp_path / "m0"
    _write_dataset(root)
    (root / "duplicates.json").unlink()

    monkeypatch.setattr(
        "sys.argv",
        ["python -m haxlab.ingestion.dataset_audit", str(root)],
    )

    assert main() == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema"] == AUDIT_SCHEMA
    assert payload["ok"] is False
    assert "duplicates:missing" in payload["issues"]


def test_real_import_output_satisfies_audit_contract(tmp_path: Path) -> None:
    import struct

    from haxlab.ingestion.pipeline import run_import

    raw = tmp_path / "raw"
    out = tmp_path / "derived"
    raw.mkdir()

    replay_name = "24-09-26-22h12-aavsmko-deadbeefcafebabe.hbr2"
    replay = raw / replay_name
    replay.write_bytes(struct.pack(">4sII", b"HBR2", 3, 600) + b"payload")

    export = {
        "channel": {"id": "726932424172371968"},
        "messages": [
            {
                "id": "1552774764110815262",
                "timestamp": "2026-09-24T22:12:27+02:00",
                "content": (
                    "MATCH REPORT SCRIM #20260924T221227749-R2\n"
                    "Red Team 3 - 2 Blue Team\n"
                    "Possession: 🔴 52.34% 🔵 47.66%"
                ),
                "attachments": [
                    {
                        "fileName": "24-09-26-22h12-aavsmko.hbr2",
                        "fileSizeBytes": replay.stat().st_size,
                    }
                ],
            }
        ],
    }
    (raw / "channel.json").write_text(
        json.dumps(export, ensure_ascii=False),
        encoding="utf-8",
    )

    run_import(raw, out)
    result = audit_dataset(out)

    assert result.ok is True
    assert result.issues == ()
    assert result.replay_count == 1
    assert result.report_count == 1
    assert result.match_count == 1


def test_audit_rejects_unsafe_relative_paths(tmp_path: Path) -> None:
    root = tmp_path / "m0"
    _write_dataset(root)

    replays = json.loads((root / "replays.json").read_text(encoding="utf-8"))
    replays[0]["source_path"] = "../escape/game.hbr2"
    (root / "replays.json").write_text(json.dumps(replays), encoding="utf-8")

    match = json.loads((root / "matches.jsonl").read_text(encoding="utf-8"))
    match["replay_source_path"] = "../escape/game.hbr2"
    (root / "matches.jsonl").write_text(json.dumps(match) + "\n", encoding="utf-8")

    result = audit_dataset(root)

    assert result.ok is False
    assert "replays:0:invalid_source_path" in result.issues


def test_audit_rejects_empty_duplicate_group(tmp_path: Path) -> None:
    root = tmp_path / "m0"
    _write_dataset(root)
    (root / "duplicates.json").write_text(
        json.dumps({SHA: []}),
        encoding="utf-8",
    )

    result = audit_dataset(root)

    assert result.ok is False
    assert f"duplicates:{SHA}:empty_group" in result.issues


def test_audit_rejects_stale_embedded_report_payload(tmp_path: Path) -> None:
    root = tmp_path / "m0"
    _write_dataset(root)

    match = json.loads((root / "matches.jsonl").read_text(encoding="utf-8"))
    match["report"]["content"] = "tampered"
    (root / "matches.jsonl").write_text(json.dumps(match) + "\n", encoding="utf-8")

    result = audit_dataset(root)

    assert result.ok is False
    assert "matches:0:report_payload_mismatch" in result.issues


def test_audit_rejects_match_id_drift(tmp_path: Path) -> None:
    root = tmp_path / "m0"
    _write_dataset(root)

    match = json.loads((root / "matches.jsonl").read_text(encoding="utf-8"))
    match["match_id"] = "stale-id"
    (root / "matches.jsonl").write_text(json.dumps(match) + "\n", encoding="utf-8")

    result = audit_dataset(root)

    assert result.ok is False
    assert "matches:0:match_id_mismatch" in result.issues

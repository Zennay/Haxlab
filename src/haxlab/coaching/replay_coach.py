from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

from haxlab.analysis.roles import ROLE_LABELS, attack_axis_x, infer_roles_4v4


SEVERITY_ORDER = {"high": 3, "medium": 2, "low": 1}


def _safe_div(num: float, den: float, default: float = 0.0) -> float:
    return num / den if den > 0 else default


def _player_minutes(player: dict[str, Any], payload: dict[str, Any]) -> float:
    sample_every = max(
        1,
        int((payload.get("simulation") or {}).get("sampleEveryTicks") or 6),
    )
    return int(player.get("samples") or 0) * sample_every / 3600.0


def _player_metrics(player: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    minutes = _player_minutes(player, payload)
    retained = int(player.get("teamTouchTransfersOut") or 0) + int(
        player.get("selfRetouches") or 0
    )
    turnovers = int(player.get("turnovers") or 0)
    pressured = int(player.get("pressuredTransitions") or 0)
    pressure_kept = int(player.get("retainedUnderPressure") or 0)
    progression_events = int(player.get("touchProgressionEvents") or 0)
    progression_sum = float(player.get("touchProgressionSum") or 0.0)
    touches = int(player.get("touches") or 0)
    transfers = int(player.get("teamTouchTransfersOut") or 0)
    kick_to_team = int(player.get("kickTransfersToTeammate") or 0)
    kick_to_opp = int(player.get("kickTransfersToOpponent") or 0)
    return {
        "minutes": round(minutes, 3),
        "touches": touches,
        "touches_per_min": _safe_div(touches, minutes),
        "retention": _safe_div(retained, retained + turnovers, default=1.0),
        "pressured_retention": (
            _safe_div(pressure_kept, pressured) if pressured > 0 else None
        ),
        "turnovers": turnovers,
        "turnovers_per_10": 10.0 * _safe_div(turnovers, minutes),
        "recoveries": int(player.get("recoveries") or 0),
        "recoveries_per_10": 10.0
        * _safe_div(int(player.get("recoveries") or 0), minutes),
        "progression_events": progression_events,
        "average_progression": _safe_div(
            progression_sum,
            progression_events,
        ),
        "team_transfers": transfers,
        "touch_goals": int(player.get("touchGoals") or 0),
        "touch_assists": int(player.get("touchAssists") or 0),
        "goal_contributions": int(player.get("touchGoals") or 0)
        + int(player.get("touchAssists") or 0),
        "kick_completion": (
            _safe_div(kick_to_team, kick_to_team + kick_to_opp)
            if kick_to_team + kick_to_opp > 0
            else None
        ),
        "average_pressure_distance": player.get("averagePressureDistance"),
        "under_pressure_touch_rate": player.get("underPressureTouchRate"),
        "average_x": player.get("averageX"),
        "average_y": player.get("averageY"),
    }


def _event_rows(payload: dict[str, Any]) -> list[list[Any]]:
    return list((payload.get("sparseEvents") or {}).get("touches") or [])


def _role_links(
    touches: list[list[Any]],
    team_id: int,
    role_by_player: dict[int, str],
) -> tuple[Counter[tuple[str, str]], Counter[int]]:
    links: Counter[tuple[str, str]] = Counter()
    player_turnovers: Counter[int] = Counter()
    for event in touches:
        if len(event) < 10:
            continue
        _, player_id, event_team, _, _, _, _, outcome, next_player_id, _ = event[:10]
        if int(event_team or 0) != team_id:
            continue
        if int(outcome or 0) == 3:
            player_turnovers[int(player_id)] += 1
        if int(outcome or 0) != 2 or next_player_id is None:
            continue
        source_role = role_by_player.get(int(player_id), "unknown")
        target_role = role_by_player.get(int(next_player_id), "unknown")
        links[(source_role, target_role)] += 1
    return links, player_turnovers


def _transition_metrics(
    touches: list[list[Any]],
    team_id: int,
) -> dict[str, Any]:
    turnovers = 0
    own_half_turnovers = 0
    final_third_turnovers = 0
    quick_recoveries = 0
    positive_progressions = 0
    big_progressions = 0
    total_progression = 0.0
    progression_events = 0

    indexed = [row for row in touches if len(row) >= 10]
    for index, event in enumerate(indexed):
        frame, _, event_team, ball_x, _, _, _, outcome, _, progression = event[:10]
        if int(event_team or 0) != team_id:
            continue

        if progression is not None:
            value = float(progression)
            total_progression += value
            progression_events += 1
            if value > 0:
                positive_progressions += 1
            if value >= 35:
                big_progressions += 1

        if int(outcome or 0) != 3:
            continue
        turnovers += 1
        if ball_x is not None:
            axis_x = attack_axis_x(team_id, float(ball_x))
            if axis_x < 0:
                own_half_turnovers += 1
            if axis_x > 120:
                final_third_turnovers += 1

        frame = int(frame or 0)
        for following in indexed[index + 1 :]:
            follow_frame = int(following[0] or 0)
            if follow_frame - frame > 180:
                break
            if int(following[2] or 0) == team_id:
                quick_recoveries += 1
                break

    return {
        "turnovers": turnovers,
        "own_half_turnovers": own_half_turnovers,
        "final_third_turnovers": final_third_turnovers,
        "quick_recoveries": quick_recoveries,
        "quick_recovery_rate": _safe_div(quick_recoveries, turnovers),
        "average_progression": _safe_div(total_progression, progression_events),
        "positive_progression_rate": _safe_div(
            positive_progressions,
            progression_events,
        ),
        "big_progressions": big_progressions,
        "progression_events": progression_events,
    }


def _priority(
    severity: str,
    area: str,
    title: str,
    finding: str,
    evidence: list[str],
    actions: list[str],
) -> dict[str, Any]:
    return {
        "severity": severity,
        "area": area,
        "title": title,
        "finding": finding,
        "evidence": evidence,
        "actions": actions,
    }


def _spacing_findings(
    role_rows: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    available = [
        (role, float(row["attack_x"]))
        for role, row in role_rows.items()
        if row.get("attack_x") is not None
    ]
    available.sort(key=lambda item: item[1])
    if len(available) < 4:
        return {"available": False}, []

    xs = {role: x for role, x in available}
    gaps = {
        "gk_dm": xs["dm"] - xs["gk"],
        "dm_am": xs["am"] - xs["dm"],
        "am_st": xs["st"] - xs["am"],
    }
    typical = median(gaps.values())
    priorities: list[dict[str, Any]] = []

    checks = (
        ("gk_dm", "GK–DM", "restverdediging en eerste opbouwlijn"),
        ("dm_am", "DM–AM", "verbinding tussen opbouw en aanval"),
        ("am_st", "AM–ST", "aansluiting rond de laatste lijn"),
    )
    for key, label, context in checks:
        gap = gaps[key]
        if gap > max(70.0, typical * 1.55):
            severity = "high" if gap > max(105.0, typical * 2.0) else "medium"
            priorities.append(
                _priority(
                    severity,
                    "spacing",
                    f"{label} staat gemiddeld te ver uit elkaar",
                    (
                        f"De gemiddelde lengteafstand van {label} is {gap:.1f}. "
                        f"Dat is duidelijk groter dan de typische linieafstand "
                        f"van {typical:.1f} in deze replay."
                    ),
                    [
                        f"{label} gemiddelde gap: {gap:.1f}",
                        f"mediane liniegap: {typical:.1f}",
                    ],
                    [
                        f"Maak de afstand tussen {label} kleiner zodra de bal van lijn wisselt.",
                        f"Coach expliciet op {context}: één speler biedt aan, de andere bewaakt de volgende zone.",
                    ],
                )
            )

    spread = xs["st"] - xs["gk"]
    return {
        "available": True,
        "attack_axis_positions": xs,
        "gaps": gaps,
        "median_gap": typical,
        "team_length": spread,
    }, priorities


def _role_feedback(
    role: str,
    player: dict[str, Any],
    metrics: dict[str, Any],
    team_metrics: dict[str, Any],
    links: Counter[tuple[str, str]],
) -> dict[str, Any]:
    strengths: list[str] = []
    improvements: list[str] = []
    drills: list[str] = []

    retention = float(metrics["retention"])
    if retention >= 0.82:
        strengths.append(f"Sterke balbehoud-score in deze replay ({retention:.0%}).")
    elif retention < 0.68:
        improvements.append(
            f"Balbehoud is kwetsbaar ({retention:.0%}); te veel possessions eindigen bij de tegenstander."
        )

    pressure_ret = metrics["pressured_retention"]
    if pressure_ret is not None:
        if pressure_ret >= 0.70:
            strengths.append(f"Goed onder druk: {pressure_ret:.0%} van gemeten pressure-transities behouden.")
        elif pressure_ret < 0.50:
            improvements.append(
                f"Onder druk zakt retention naar {pressure_ret:.0%}; sneller beslissen of eerder steun zoeken."
            )

    turnovers_per_10 = float(metrics["turnovers_per_10"])
    if turnovers_per_10 >= 4.0:
        improvements.append(
            f"Hoge turnoverfrequentie: {turnovers_per_10:.1f} per 10 actieve minuten."
        )

    avg_prog = float(metrics["average_progression"])
    recoveries = int(metrics["recoveries"])
    goals = int(metrics["touch_goals"])
    assists = int(metrics["touch_assists"])

    if role == "gk":
        if turnovers_per_10 > 2.5:
            improvements.append("GK-distributie is te risicovol; eerste pass moet vaker possession-first zijn.")
        if links[("gk", "dm")] == 0 and metrics["team_transfers"] > 0:
            improvements.append("Geen gemeten GK→DM touch-transfer; de veilige eerste opbouwlijn wordt nauwelijks gebruikt.")
        drills.extend(
            [
                "GK+DM outlet drill: na elke save/losse bal eerst veilige DM-hoek openen.",
                "2-keuze-regel: onder druk direct clearen of DM inspelen; geen derde risicovolle touch.",
            ]
        )
    elif role == "dm":
        if recoveries >= 3:
            strengths.append(f"DM leverde {recoveries} recoveries en was zichtbaar in de restverdediging.")
        if avg_prog <= 0:
            improvements.append(
                f"Gemiddelde touch-progression is {avg_prog:.1f}; DM brengt possession te weinig vooruit."
            )
        if links[("dm", "am")] + links[("am", "dm")] <= 1:
            improvements.append("DM–AM verbinding is vrijwel afwezig; hierdoor wordt de opbouw snel direct/voorspelbaar.")
        drills.extend(
            [
                "DM scan-drill: vóór ontvangst al keuze vooruit/terug vastleggen.",
                "Transition rule: bij AM/ST-balverlies eerst centrale lane sluiten, daarna pas uitstappen.",
            ]
        )
    elif role == "am":
        if avg_prog > 5:
            strengths.append(f"Positieve progression vanuit AM ({avg_prog:.1f} per gemeten touch-transitie).")
        if assists > 0:
            strengths.append(f"{assists} assist-evidence vanuit touch chains.")
        if links[("am", "st")] + links[("st", "am")] <= 1:
            improvements.append("AM–ST combinatievolume is laag; ST dreigt geïsoleerd te raken.")
        drills.extend(
            [
                "AM half-turn drill: ontvangen met optie terug naar DM én door naar ST.",
                "Third-man patroon: DM→AM→ST en direct doorbewegen voor de tweede bal.",
            ]
        )
    elif role == "st":
        if goals > 0:
            strengths.append(f"{goals} goal(s) direct aan ST-touch evidence gekoppeld.")
        if metrics["touches_per_min"] < 0.55:
            improvements.append(
                f"ST heeft weinig betrokkenheid ({metrics['touches_per_min']:.2f} touches/min); check timing en aansluiting met AM."
            )
        if avg_prog < -5:
            improvements.append("ST-touch sequences verliezen gemiddeld terrein; vaker kaatsen of bal beschermen i.p.v. teruggedrongen worden.")
        drills.extend(
            [
                "ST wall-pass drill met AM: één touch kaats, daarna direct nieuwe looplijn.",
                "Finishing-positioning: blijf hoog genoeg om diepte te geven, maar kom alleen in als AM daadwerkelijk steun nodig heeft.",
            ]
        )

    if not strengths:
        strengths.append("Geen extreme positieve uitschieter; beoordeling blijft bewust evidence-first voor één replay.")
    if not improvements:
        improvements.append("Geen duidelijke rode vlag op de huidige heuristieken; focus op herhaalbaarheid over meerdere replays.")

    return {
        "player_id": int(player.get("id")),
        "name": player.get("name"),
        "role": role,
        "role_label": ROLE_LABELS.get(role, role.upper()),
        "metrics": metrics,
        "strengths": strengths,
        "improvements": improvements,
        "drills": drills,
    }


def analyze_team(payload: dict[str, Any], team_id: int) -> dict[str, Any]:
    if team_id not in (1, 2):
        raise ValueError("team_id must be 1 (red) or 2 (blue)")

    players = [
        player
        for player in list(payload.get("players") or [])
        if int(player.get("teamId") or 0) == team_id and int(player.get("samples") or 0) > 0
    ]
    assignments = infer_roles_4v4(list(payload.get("players") or []))
    role_by_player = {
        int(player["id"]): assignments.get(int(player["id"]), {}).get("role", "unknown")
        for player in players
    }

    role_rows: dict[str, dict[str, Any]] = {}
    per_player: list[dict[str, Any]] = []
    metrics_by_player: dict[int, dict[str, Any]] = {}
    for player in players:
        player_id = int(player["id"])
        assignment = assignments.get(player_id, {})
        role = str(assignment.get("role") or "unknown")
        metrics = _player_metrics(player, payload)
        metrics_by_player[player_id] = metrics
        if role != "unknown":
            role_rows[role] = {
                "player_id": player_id,
                "name": player.get("name"),
                "attack_x": assignment.get("attack_x"),
                "confidence": assignment.get("confidence", 0.0),
                "reason": assignment.get("reason"),
            }

    touches = _event_rows(payload)
    links, player_turnovers = _role_links(touches, team_id, role_by_player)
    transition = _transition_metrics(touches, team_id)

    team_retained = sum(
        int(p.get("teamTouchTransfersOut") or 0) + int(p.get("selfRetouches") or 0)
        for p in players
    )
    team_turnovers = sum(int(p.get("turnovers") or 0) for p in players)
    pressure_total = sum(int(p.get("pressuredTransitions") or 0) for p in players)
    pressure_kept = sum(int(p.get("retainedUnderPressure") or 0) for p in players)
    recoveries = sum(int(p.get("recoveries") or 0) for p in players)
    transfers = sum(int(p.get("teamTouchTransfersOut") or 0) for p in players)
    total_minutes = int(payload.get("totalFrames") or 0) / 3600.0
    simulation = payload.get("simulation") or {}
    goals = (simulation.get("teamGoals") or {}).get("red" if team_id == 1 else "blue", 0)

    team_metrics = {
        "team_id": team_id,
        "team": "red" if team_id == 1 else "blue",
        "match_minutes": round(total_minutes, 3),
        "goals": int(goals or 0),
        "retention": _safe_div(
            team_retained,
            team_retained + team_turnovers,
            default=1.0,
        ),
        "pressured_retention": (
            _safe_div(pressure_kept, pressure_total) if pressure_total > 0 else None
        ),
        "turnovers": team_turnovers,
        "recoveries": recoveries,
        "team_transfers": transfers,
        "recoveries_per_10_match_min": 10.0 * _safe_div(recoveries, total_minutes),
    }

    spacing, priorities = _spacing_findings(role_rows)

    if team_metrics["retention"] < 0.68:
        priorities.append(
            _priority(
                "high",
                "possession",
                "Te veel possessions eindigen in balverlies",
                f"Team retention is slechts {team_metrics['retention']:.0%}.",
                [
                    f"retention: {team_metrics['retention']:.1%}",
                    f"turnovers: {team_turnovers}",
                ],
                [
                    "Maak de eerste veilige optie zichtbaar vóór de baldrager onder druk komt.",
                    "DM en AM moeten nooit tegelijk dezelfde verticale zone verlaten.",
                    "Gebruik ST vaker als kaatsstation in plaats van elke aanval direct te forceren.",
                ],
            )
        )
    elif team_metrics["retention"] < 0.80:
        priorities.append(
            _priority(
                "medium",
                "possession",
                "Balbehoud kan stabieler",
                f"Team retention is {team_metrics['retention']:.0%}; bruikbaar, maar er lekt nog te veel possession weg.",
                [f"turnovers: {team_turnovers}"],
                [
                    "Verminder onnodige 50/50-kicks in de eigen en middelste zone.",
                    "Na ontvangst onder druk: kaats of verplaats vóór de tweede tegenstander aansluit.",
                ],
            )
        )

    if transition["own_half_turnovers"] >= 3:
        priorities.append(
            _priority(
                "high",
                "risk",
                "Balverlies in eigen helft is een directe structurele dreiging",
                (
                    f"{transition['own_half_turnovers']} turnovers vonden plaats aan de eigen kant "
                    "van de attack-axis."
                ),
                [
                    f"own-half turnovers: {transition['own_half_turnovers']}",
                    f"all measured turnovers: {transition['turnovers']}",
                ],
                [
                    "GK/DM: in eigen helft possession-first; alleen verticaal spelen als AM echt vrij staat.",
                    "AM moet bij risicovolle DM-bal een zichtbare terugpass-lijn houden.",
                ],
            )
        )

    if transition["turnovers"] >= 3 and transition["quick_recovery_rate"] < 0.35:
        priorities.append(
            _priority(
                "high",
                "transition",
                "Counterpress na balverlies is te traag of te open",
                (
                    f"Slechts {transition['quick_recovery_rate']:.0%} van gemeten turnovers "
                    "werd binnen ongeveer 3 seconden hersteld."
                ),
                [
                    f"quick recoveries: {transition['quick_recoveries']}/{transition['turnovers']}",
                ],
                [
                    "Eerste speler vertraagt de tegenstander; tweede speler sluit de centrale lane.",
                    "ST hoeft niet blind terug: positioneer hem als outlet terwijl DM/AM de eerste recovery doen.",
                ],
            )
        )
    elif transition["turnovers"] >= 3 and transition["quick_recovery_rate"] >= 0.60:
        priorities.append(
            _priority(
                "low",
                "transition",
                "Counterpress is een duidelijke kracht",
                (
                    f"{transition['quick_recovery_rate']:.0%} van gemeten turnovers "
                    "wordt snel hersteld."
                ),
                [f"quick recoveries: {transition['quick_recoveries']}"],
                [
                    "Behoud deze agressie, maar bewaak dat DM niet tegelijk met AM voorbij de bal komt.",
                ],
            )
        )

    pressure_ret = team_metrics["pressured_retention"]
    if pressure_ret is not None and pressure_ret < 0.50:
        priorities.append(
            _priority(
                "high",
                "pressure",
                "Onder druk stort de possessionkwaliteit in",
                f"Pressured retention is {pressure_ret:.0%}.",
                [f"pressure transitions: {pressure_total}"],
                [
                    "Train één-touch steunhoeken tussen DM en AM.",
                    "Maak vooraf duidelijke trigger: bij twee tegenstanders binnen pressure-range direct kaatsen/clearen.",
                ],
            )
        )

    essential_links = {
        "GK↔DM": links[("gk", "dm")] + links[("dm", "gk")],
        "DM↔AM": links[("dm", "am")] + links[("am", "dm")],
        "AM↔ST": links[("am", "st")] + links[("st", "am")],
    }
    if transfers >= 12:
        for label, count in essential_links.items():
            if count <= 1:
                priorities.append(
                    _priority(
                        "medium",
                        "connectivity",
                        f"{label} verbinding is bijna afwezig",
                        (
                            f"Slechts {count} directe touch-transfer(s) op {transfers} "
                            "teamtransfers."
                        ),
                        [f"{label}: {count}", f"team transfers: {transfers}"],
                        [
                            f"Bouw een vast steunpatroon voor {label}: ontvanger beweegt vóór de pass al in een nieuwe hoek.",
                            "Vermijd dat beide spelers tegelijk van elkaar weg bewegen tijdens balprogressie.",
                        ],
                    )
                )

    for player in players:
        player_id = int(player["id"])
        role = role_by_player[player_id]
        feedback = _role_feedback(
            role,
            player,
            metrics_by_player[player_id],
            team_metrics,
            links,
        )
        feedback["observed_turnovers_from_sparse_events"] = player_turnovers[player_id]
        feedback["role_confidence"] = assignments.get(player_id, {}).get("confidence", 0.0)
        per_player.append(feedback)

    per_player.sort(
        key=lambda row: (
            ("gk", "dm", "am", "st", "unknown").index(row["role"])
            if row["role"] in ("gk", "dm", "am", "st", "unknown")
            else 99
        )
    )

    priorities.sort(
        key=lambda row: (
            SEVERITY_ORDER.get(row["severity"], 0),
            row["area"],
        ),
        reverse=True,
    )

    top_actions: list[str] = []
    for priority in priorities:
        if priority["severity"] not in {"high", "medium"}:
            continue
        for action in priority["actions"]:
            if action not in top_actions:
                top_actions.append(action)
            if len(top_actions) >= 6:
                break
        if len(top_actions) >= 6:
            break

    return {
        "team": team_metrics,
        "role_assignments": role_rows,
        "spacing": spacing,
        "transition": transition,
        "transfer_links": {
            f"{source}->{target}": count
            for (source, target), count in sorted(links.items())
        },
        "essential_links": essential_links,
        "priorities": priorities,
        "players": per_player,
        "training_plan": top_actions,
    }


def analyze_replay(payload: dict[str, Any], teams: list[int] | None = None) -> dict[str, Any]:
    teams = teams or [1, 2]
    return {
        "schema": "haxlab-team-coach-v1",
        "source_file": payload.get("sourceFile"),
        "schema_version": payload.get("schemaVersion"),
        "feature_version": payload.get("featureVersion"),
        "match": {
            "total_frames": int(payload.get("totalFrames") or 0),
            "minutes": round(int(payload.get("totalFrames") or 0) / 3600.0, 3),
            "team_goals": (payload.get("simulation") or {}).get("teamGoals"),
            "touches": (payload.get("featureSummary") or {}).get("touches"),
            "turnovers": (payload.get("featureSummary") or {}).get("turnovers"),
        },
        "teams": [analyze_team(payload, team_id) for team_id in teams],
    }


def render_text(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("HAXLAB TEAM COACH — INTENSIVE 4v4 REVIEW")
    lines.append("=" * 72)
    match = report["match"]
    lines.append(
        f"Replay: {report.get('source_file') or '<analysis-json>'} | "
        f"{match['minutes']:.1f} min | goals={match.get('team_goals')}"
    )
    lines.append(
        "Model: evidence-first coach; vaste 4v4-structuur GK–DM–AM–ST. "
        "Dit is replaycoaching, geen absolute waarheid over spelerkwaliteit."
    )

    for team_report in report["teams"]:
        team = team_report["team"]
        lines.append("")
        lines.append("#" * 72)
        lines.append(f"TEAM {team['team'].upper()} — COACH REVIEW")
        lines.append("#" * 72)
        lines.append(
            f"Goals {team['goals']} | retention {team['retention']:.1%} | "
            f"turnovers {team['turnovers']} | recoveries {team['recoveries']} | "
            f"team transfers {team['team_transfers']}"
        )
        if team["pressured_retention"] is not None:
            lines.append(f"Retention onder druk: {team['pressured_retention']:.1%}")

        lines.append("")
        lines.append("1) ROLE MAP & TEAM SHAPE")
        lines.append("-" * 72)
        for role in ("gk", "dm", "am", "st"):
            row = team_report["role_assignments"].get(role)
            if row:
                lines.append(
                    f"{ROLE_LABELS[role]:>2}: {row['name']} | attack-x {row['attack_x']:.1f} | "
                    f"role confidence {row['confidence']:.0%}"
                )
        spacing = team_report["spacing"]
        if spacing.get("available"):
            lines.append(
                "Line gaps: "
                f"GK-DM {spacing['gaps']['gk_dm']:.1f}, "
                f"DM-AM {spacing['gaps']['dm_am']:.1f}, "
                f"AM-ST {spacing['gaps']['am_st']:.1f}; "
                f"team length {spacing['team_length']:.1f}"
            )

        lines.append("")
        lines.append("2) BUILD-UP & CONNECTIVITY")
        lines.append("-" * 72)
        for label, value in team_report["essential_links"].items():
            lines.append(f"{label}: {value} directe touch-transfers")
        trans = team_report["transition"]
        lines.append(
            f"Gemiddelde progression {trans['average_progression']:.2f} | "
            f"positieve progression {trans['positive_progression_rate']:.1%} | "
            f"grote progressions {trans['big_progressions']}"
        )

        lines.append("")
        lines.append("3) TRANSITIONS & RISK")
        lines.append("-" * 72)
        lines.append(
            f"Turnovers {trans['turnovers']} | eigen helft {trans['own_half_turnovers']} | "
            f"laatste zone {trans['final_third_turnovers']} | "
            f"quick recoveries {trans['quick_recoveries']} "
            f"({trans['quick_recovery_rate']:.1%})"
        )

        lines.append("")
        lines.append("4) COACH PRIORITIES")
        lines.append("-" * 72)
        if not team_report["priorities"]:
            lines.append("Geen duidelijke teamrode-vlag gevonden met de huidige evidence.")
        for index, item in enumerate(team_report["priorities"], 1):
            lines.append(
                f"{index}. [{item['severity'].upper()}] {item['title']} ({item['area']})"
            )
            lines.append(f"   Diagnose: {item['finding']}")
            for evidence in item["evidence"]:
                lines.append(f"   Evidence: {evidence}")
            for action in item["actions"]:
                lines.append(f"   Coachactie: {action}")

        lines.append("")
        lines.append("5) PLAYER-BY-PLAYER COACHING")
        lines.append("-" * 72)
        for player in team_report["players"]:
            m = player["metrics"]
            lines.append(
                f"{player['role_label']} — {player['name']} "
                f"(role confidence {player['role_confidence']:.0%})"
            )
            lines.append(
                f"   touches/min {m['touches_per_min']:.2f} | retention {m['retention']:.1%} | "
                f"turnovers/10 {m['turnovers_per_10']:.2f} | recoveries {m['recoveries']} | "
                f"avg progression {m['average_progression']:.2f}"
            )
            if m["pressured_retention"] is not None:
                lines.append(f"   pressured retention {m['pressured_retention']:.1%}")
            for value in player["strengths"]:
                lines.append(f"   + {value}")
            for value in player["improvements"]:
                lines.append(f"   ! {value}")
            for value in player["drills"]:
                lines.append(f"   > Drill: {value}")

        lines.append("")
        lines.append("6) TRAINING PLAN — WHAT TO FIX FIRST")
        lines.append("-" * 72)
        if team_report["training_plan"]:
            for index, action in enumerate(team_report["training_plan"], 1):
                lines.append(f"{index}. {action}")
        else:
            lines.append("Gebruik meerdere replays om stabiele teamprioriteiten te vinden.")

    return "\n".join(lines) + "\n"


def _load_input(path: Path, decoder_script: Path, sample_every_ticks: int) -> dict[str, Any]:
    if path.suffix.lower() != ".hbr2":
        return json.loads(path.read_text(encoding="utf-8"))

    completed = subprocess.run(
        [
            "node",
            str(decoder_script),
            str(path),
            str(max(1, sample_every_ticks)),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            f"Replay decoder failed ({completed.returncode}): "
            f"{completed.stderr.strip()[-4000:]}"
        )
    return json.loads(completed.stdout)


def _team_from_player(payload: dict[str, Any], player_name: str) -> int:
    wanted = " ".join(player_name.strip().split()).casefold()
    matches = [
        int(player.get("teamId") or 0)
        for player in payload.get("players") or []
        if " ".join(str(player.get("name") or "").strip().split()).casefold() == wanted
        and int(player.get("teamId") or 0) in (1, 2)
    ]
    if not matches:
        raise ValueError(f"Player {player_name!r} not found in replay")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(prog="haxlab-coach")
    parser.add_argument("replay", type=Path, help="state-pass-v4 JSON or raw .hbr2 replay")
    parser.add_argument(
        "--team",
        choices=["red", "blue", "1", "2", "both"],
        default="both",
    )
    parser.add_argument(
        "--team-player",
        default=None,
        help="Analyze the team containing this exact display name.",
    )
    parser.add_argument(
        "--decoder-script",
        type=Path,
        default=Path("/opt/haxlab/tools/decode_replay.js"),
    )
    parser.add_argument("--sample-every-ticks", type=int, default=6)
    parser.add_argument("--format", choices=["text", "json"], default="text")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    payload = _load_input(
        args.replay,
        args.decoder_script,
        max(1, args.sample_every_ticks),
    )
    if args.team_player:
        teams = [_team_from_player(payload, args.team_player)]
    elif args.team == "both":
        teams = [1, 2]
    else:
        teams = [1 if args.team in {"red", "1"} else 2]

    report = analyze_replay(payload, teams)
    rendered = (
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        if args.format == "json"
        else render_text(report)
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

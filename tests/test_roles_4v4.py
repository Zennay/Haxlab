from haxlab.analysis.roles import infer_roles_4v4


def _p(player_id: int, team: int, x: float, samples: int = 6000) -> dict:
    return {
        "id": player_id,
        "name": f"P{player_id}",
        "teamId": team,
        "samples": samples,
        "averageX": x,
    }


def test_exact_4v4_assigns_gk_dm_am_st_on_attack_axis() -> None:
    players = [
        _p(1, 1, -150),
        _p(2, 1, -50),
        _p(3, 1, 45),
        _p(4, 1, 140),
        _p(5, 2, 150),
        _p(6, 2, 50),
        _p(7, 2, -45),
        _p(8, 2, -140),
    ]

    roles = infer_roles_4v4(players)

    assert [roles[i]["role"] for i in (1, 2, 3, 4)] == ["gk", "dm", "am", "st"]
    assert [roles[i]["role"] for i in (5, 6, 7, 8)] == ["gk", "dm", "am", "st"]
    assert all(roles[i]["confidence"] >= 0.7 for i in range(1, 9))


def test_substitute_is_attached_to_nearest_core_role() -> None:
    players = [
        _p(1, 1, -150, 9000),
        _p(2, 1, -50, 9000),
        _p(3, 1, 45, 9000),
        _p(4, 1, 140, 9000),
        _p(9, 1, -45, 800),
    ]

    roles = infer_roles_4v4(players)

    assert roles[9]["role"] == "dm"
    assert roles[9]["reason"] == "substitute_nearest_role_anchor"
    assert roles[9]["confidence"] < roles[2]["confidence"]

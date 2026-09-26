#!/usr/bin/env python3
import json, subprocess, sys
from pathlib import Path

POLICY = Path("/home/ubuntu/zennay-cloud/resource-policy.json")
WEIGHTS = {"background": 100, "normal": 400, "high": 800}
UNITS = {
    "haxlab": [
        "haxlab-analyzer.service",
        "haxlab-ingest.service",
        "haxlab-worker.service",
        "actions.runner.Zennay-Haxlab.vps-bb300bba-haxlab.service",
    ],
    "ftmo": [
        "ftmo-autonomous.service",
        "actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service",
    ],
    "cloud": ["zennay-cloud.service"],
    "supa": [],
}

def main():
    if len(sys.argv) != 3 or sys.argv[1] != "apply":
        raise SystemExit("usage: zennay-resource-control apply <project|all>")
    target = sys.argv[2]
    policy = json.loads(POLICY.read_text())
    targets = list(UNITS) if target == "all" else [target]
    for project in targets:
        if project not in UNITS:
            raise SystemExit("unknown project")
        priority = (policy.get(project) or {}).get("priority", "normal")
        if priority not in WEIGHTS:
            raise SystemExit("invalid priority")
        weight = str(WEIGHTS[priority])
        for unit in UNITS[project]:
            subprocess.run(["systemctl", "set-property", unit, "CPUWeight=" + weight, "IOWeight=" + weight], check=True)
        print(project, priority, weight)

if __name__ == "__main__":
    main()

from pathlib import Path
import json, os, re, shutil, subprocess, time, urllib.request, zipfile

ROOT = Path("/home/ubuntu/zennay-cloud")
SRC = Path(__file__).resolve().parent
STAMP = time.strftime("%Y%m%d-%H%M%S")

def run(args, check=True, capture=False, env=None):
    kw = {"check": check, "text": True, "env": env}
    if capture:
        kw["stdout"] = subprocess.PIPE
        kw["stderr"] = subprocess.STDOUT
    return subprocess.run(args, **kw)

def backup(path):
    p = Path(path)
    if p.exists():
        shutil.copy2(p, p.with_name(p.name + ".bak-" + STAMP))

def replace_once(path, old, new):
    p = Path(path)
    s = p.read_text()
    if old not in s:
        raise RuntimeError("anchor not found in %s: %r" % (p, old[:100]))
    p.write_text(s.replace(old, new, 1))

def ensure_contains(path, marker, transform):
    p = Path(path)
    s = p.read_text()
    if marker not in s:
        p.write_text(transform(s))

for rel in [
    "projects.json","project-layout.json","server.py","public/index.html",
    "wear-os/app/build.gradle.kts","wear-os/app/src/main/AndroidManifest.xml",
    "wear-os/app/src/main/java/com/zennay/cloud/watch/MainActivity.kt",
    "wear-os/app/src/main/java/com/zennay/cloud/watch/ui/ZennayWearApp.kt",
]:
    backup(ROOT / rel)

# Install staged files.
shutil.copy2(SRC / "zennay_enhancements.py", ROOT / "enhancements.py")
shutil.copy2(SRC / "zennay_ui.js", ROOT / "public/enhancements.js")
shutil.copy2(SRC / "zennay_ui.css", ROOT / "public/enhancements.css")
notif_dir = ROOT / "wear-os/app/src/main/java/com/zennay/cloud/watch/notifications"
notif_dir.mkdir(parents=True, exist_ok=True)
shutil.copy2(SRC / "AlertWorker.kt", notif_dir / "AlertWorker.kt")
shutil.copy2(SRC / "MainActivity.kt", ROOT / "wear-os/app/src/main/java/com/zennay/cloud/watch/MainActivity.kt")

# Granular project progress.
projects_path = ROOT / "projects.json"
projects = json.loads(projects_path.read_text())
profiles = {
    "haxlab": {
        "phase": "Live champion validation · competitive behavior",
        "next_step": "Verbeter kickgedrag en competitieve benchmarkresultaten; valideer daarna tegen echte spelers.",
        "progress_basis": "Bouwvoortgang is het gemiddelde van milestone-percentages. Modelkwaliteit staat apart en komt uit frozen-holdout en live-runtime evidence.",
        "milestone_revision": "v2-granular",
        "progresses": [100,100,100,100,90,15,40,55],
    },
    "ftmo": {
        "phase": "Generation 10 candidate · awaiting walk-forward",
        "next_step": "Voer de frozen walk-forward uit voor de Generation-10 impulse-reversal candidate; houd development en validatie strikt gescheiden.",
        "progress_basis": "Bouwvoortgang is het gemiddelde van milestone-percentages. Tradingkwaliteit staat apart als development-, walk-forward- en frozen-holdout evidence.",
        "milestone_revision": "v2-granular",
        "progresses": [100,100,100,100,100,100,85,45],
    },
    "supa": {
        "progress_basis": "Bouwvoortgang is het gemiddelde van milestone-percentages; onderzoek en UX zijn afgerond, productiebouw wordt apart gevolgd.",
        "milestone_revision": "supa-v2-granular",
        "progresses": [100,100,100,100,0,0,0,0],
    },
    "cloud": {
        "status": "archived",
        "phase": "Archived · control room operational",
        "progress_basis": "Zennay Cloud is operationeel en wordt als infrastructuur/control room gearchiveerd, niet als actief productproject meegeteld.",
        "milestone_revision": "v3-archived",
        "progresses": [100,100,100,100,100,100,100],
    },
}
for p in projects:
    cfg = profiles.get(p["id"])
    if not cfg:
        continue
    for key in ("phase","next_step","progress_basis","milestone_revision","status"):
        if key in cfg:
            p[key] = cfg[key]
    for m, value in zip(p["milestones"], cfg["progresses"]):
        m["progress"] = value
        m["done"] = value >= 100
projects_path.write_text(json.dumps(projects, ensure_ascii=False, indent=2) + "\n")

# Archive Cloud in layout.
layout_path = ROOT / "project-layout.json"
layout = json.loads(layout_path.read_text()) if layout_path.exists() else {"order":[p["id"] for p in projects],"archived":[]}
valid = {p["id"] for p in projects}
layout["order"] = [x for x in layout.get("order", []) if x in valid]
layout["order"] += [p["id"] for p in projects if p["id"] not in layout["order"]]
layout["archived"] = list(dict.fromkeys([x for x in layout.get("archived", []) if x in valid] + ["cloud"]))
layout_path.write_text(json.dumps(layout, ensure_ascii=False, indent=2) + "\n")

# Resource policy defaults requested by user.
policy_path = ROOT / "resource-policy.json"
try:
    policy = json.loads(policy_path.read_text())
except Exception:
    policy = {}
policy.setdefault("supa", {"priority":"normal"})
policy.setdefault("cloud", {"priority":"normal"})
policy["haxlab"] = {"priority":"background"}
policy["ftmo"] = {"priority":"high"}
policy_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2) + "\n")

# Install narrow resource helper and sudo rule.
run(["sudo","install","-m","0755",str(SRC/"zennay_resource_control.py"),"/usr/local/sbin/zennay-resource-control"])
sudoers = Path("/tmp/zennay-resource-control.sudoers")
sudoers.write_text("ubuntu ALL=(root) NOPASSWD: /usr/local/sbin/zennay-resource-control *\n")
run(["sudo","install","-m","0440",str(sudoers),"/etc/sudoers.d/zennay-resource-control"])
run(["sudo","visudo","-cf","/etc/sudoers.d/zennay-resource-control"])

# Patch server once.
server = ROOT / "server.py"
s = server.read_text()
if "import enhancements" not in s:
    s = s.replace(
        "import json, os, sqlite3, subprocess, shutil, threading, time, mimetypes, logging\n",
        "import json, os, sqlite3, subprocess, shutil, threading, time, mimetypes, logging\nimport enhancements\n",
        1
    )
    s = s.replace(
        "        c.execute('CREATE INDEX IF NOT EXISTS runner_events_ts ON runner_events(ts)')\n",
        "        c.execute('CREATE INDEX IF NOT EXISTS runner_events_ts ON runner_events(ts)')\n        enhancements.init_db(c)\n",
        1
    )
    s = s.replace(
        "def public_status(data):\n    visible, archived, layout=project_views(data)\n    return {**data,'projects':visible,'archived_projects':archived,'project_layout':layout}\n",
        "def public_status(data):\n    visible, archived, layout=project_views(data)\n    return {**data,'projects':visible,'archived_projects':archived,'project_layout':layout,'alerts':enhancements.list_alerts(DB,8,False)}\n",
        1
    )
    old = """    events=[]
    for p in projects:
        p['progress']=round(sum(m['done'] for m in p['milestones'])/len(p['milestones'])*100)
        p['completed']=sum(m['done'] for m in p['milestones'])
        p['next']=next((m['title'] for m in p['milestones'] if not m['done']), 'Alle milestones afgerond')
"""
    new = """    events=[]
    resources=enhancements.resource_snapshot()
    for p in projects:
        values=[float(m.get('progress',100 if m.get('done') else 0)) for m in p['milestones']]
        for m,value in zip(p['milestones'],values):
            m['progress']=round(max(0,min(100,value)),1);m['done']=m['progress']>=100
        p['progress']=round(sum(m['progress'] for m in p['milestones'])/len(p['milestones']))
        p['completed']=sum(m['done'] for m in p['milestones'])
        p['next']=next((m['title'] for m in p['milestones'] if not m['done']), 'Alle milestones afgerond')
        p['current_milestone_progress']=next((m['progress'] for m in p['milestones'] if not m['done']),100)
        p['resource']=resources.get(p['id'],{})
"""
    if old not in s:
        raise RuntimeError("collect progress anchor missing")
    s = s.replace(old, new, 1)
    old = """        if p['id']=='haxlab':
            names=['haxlab-analyzer.service','haxlab-ingest.service','haxlab-worker.service',RUNNERS['haxlab']]
            try:p['metrics']=replay_metrics()
            except Exception:p['metrics']={'available':False}
        elif p['id']=='ftmo': names=['ftmo-autonomous.timer','ftmo-autonomous.service',RUNNERS['ftmo']]
"""
    new = """        if p['id']=='haxlab':
            names=['haxlab-analyzer.service','haxlab-ingest.service','haxlab-worker.service',RUNNERS['haxlab']]
            try:p['metrics']=replay_metrics()
            except Exception:p['metrics']={'available':False}
            try:p['quality']=enhancements.quality_for('haxlab')
            except Exception:p['quality']={'available':False,'items':[]}
        elif p['id']=='ftmo':
            names=['ftmo-autonomous.timer','ftmo-autonomous.service',RUNNERS['ftmo']]
            try:p['quality']=enhancements.quality_for('ftmo')
            except Exception:p['quality']={'available':False,'items':[]}
"""
    if old not in s:
        raise RuntimeError("quality anchor missing")
    s = s.replace(old, new, 1)
    old = "    return {'version':2,'time':now(),'host':host_metrics(),'projects':projects,'errors':errors,'sampling':{'host_seconds':15,'projects_seconds':60,'history_seconds':300},'timezone':'Europe/Amsterdam'}\n"
    new = "    data={'version':3,'time':now(),'host':host_metrics(),'projects':projects,'errors':errors,'sampling':{'host_seconds':15,'projects_seconds':60,'history_seconds':300},'timezone':'Europe/Amsterdam'}\n    try: enhancements.evaluate_alerts(data,runner_status(),DB)\n    except Exception: logging.exception('Alert evaluation failed')\n    return data\n"
    if old not in s:
        raise RuntimeError("collect return anchor missing")
    s = s.replace(old, new, 1)
    s = s.replace(
        "        'chatgpt_runner':runner,\n        'projects':projects\n",
        "        'chatgpt_runner':runner,\n        'alerts':enhancements.list_alerts(DB,5,True),\n        'projects':projects\n",
        1
    )
    s = s.replace(
        "            'services':p.get('services',[])\n",
        "            'services':p.get('services',[]),\n            'quality':p.get('quality'),\n            'resource':p.get('resource'),\n            'milestones':p.get('milestones',[])\n",
        1
    )
    s = s.replace(
        "            if u.path=='/api/project-layout':\n",
        "            if u.path=='/api/resource-priority':\n                project=str(payload.get('project') or '')\n                priority=str(payload.get('priority') or '')\n                try: result=enhancements.set_priority(project,priority)\n                except ValueError as e: return self.reply({'error':str(e)},400)\n                return self.reply({'ok':True,'resource':result,'time':now()})\n            if u.path=='/api/project-layout':\n",
        1
    )
    s = s.replace(
        "            if u.path=='/api/v1/watch':return self.reply(watch_summary(data))\n",
        "            if u.path=='/api/v1/watch':return self.reply(watch_summary(data))\n            if u.path=='/api/v1/alerts':return self.reply({'time':data['time'],'alerts':enhancements.list_alerts(DB,20,True)})\n            if u.path=='/api/alerts':return self.reply(enhancements.list_alerts(DB,20,False))\n            if u.path=='/api/resources':return self.reply(enhancements.resource_snapshot())\n",
        1
    )
    server.write_text(s)


# Enforce local-only resource mutation until dashboard authentication exists.
s = server.read_text()
needle = "            if u.path=='/api/resource-priority':\n                project=str(payload.get('project') or '')\n"
if needle in s:
    s = s.replace(
        needle,
        "            if u.path=='/api/resource-priority':\n                if self.client_address[0] not in ('127.0.0.1','::1'):return self.reply({'error':'Alleen lokaal'},403)\n                project=str(payload.get('project') or '')\n",
        1
    )
    server.write_text(s)

# Add UI enhancement assets after the existing app/style.
index = ROOT / "public/index.html"
html = index.read_text()
if "/enhancements.css" not in html:
    html = html.replace('<link rel="stylesheet" href="/style.css">', '<link rel="stylesheet" href="/style.css"><link rel="stylesheet" href="/enhancements.css">')
if "/enhancements.js" not in html:
    html = html.replace('<script src="/app.js" defer></script>', '<script src="/app.js" defer></script><script src="/enhancements.js" defer></script>')
index.write_text(html)

# Watch metric pills: label above value.
ui = ROOT / "wear-os/app/src/main/java/com/zennay/cloud/watch/ui/ZennayWearApp.kt"
u = ui.read_text()
pattern = re.compile(r"""@Composable
private fun MetricPill\(label: String, value: String\) \{
    Row\(
        modifier = Modifier
            \.background\(Surface2, RoundedCornerShape\(20\.dp\)\)
            \.padding\(horizontal = 9\.dp, vertical = 5\.dp\),
        verticalAlignment = Alignment\.CenterVertically
    \) \{
        Text\(label, color = TextMuted, fontSize = 8\.sp, fontWeight = FontWeight\.Bold\)
        Spacer\(Modifier\.width\(5\.dp\)\)
        Text\(value, color = TextMain, fontSize = 10\.sp, fontWeight = FontWeight\.Bold\)
    \}
\}""")
replacement = """@Composable
private fun MetricPill(label: String, value: String) {
    Column(
        modifier = Modifier
            .width(62.dp)
            .background(Surface2, RoundedCornerShape(20.dp))
            .padding(horizontal = 7.dp, vertical = 6.dp),
        horizontalAlignment = Alignment.CenterHorizontally
    ) {
        Text(label, color = TextMuted, fontSize = 7.sp, fontWeight = FontWeight.Bold)
        Spacer(Modifier.height(1.dp))
        Text(value, color = TextMain, fontSize = 10.sp, fontWeight = FontWeight.Bold)
    }
}"""
u2, n = pattern.subn(replacement, u, count=1)
if n == 0 and ".width(62.dp)" not in u:
    raise RuntimeError("MetricPill patch anchor missing")
ui.write_text(u2 if n else u)

# Watch notifications dependencies/version/permission.
gradle = ROOT / "wear-os/app/build.gradle.kts"
g = gradle.read_text()
g = g.replace('versionCode = 2', 'versionCode = 3')
g = g.replace('versionName = "0.2.0"', 'versionName = "0.3.0"')
if "androidx.work:work-runtime-ktx" not in g:
    g = g.replace(
        'implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")',
        'implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.10.2")\n    implementation("androidx.work:work-runtime-ktx:2.10.0")'
    )
gradle.write_text(g)

manifest = ROOT / "wear-os/app/src/main/AndroidManifest.xml"
m = manifest.read_text()
if "POST_NOTIFICATIONS" not in m:
    m = m.replace(
        '<uses-permission android:name="android.permission.INTERNET" />',
        '<uses-permission android:name="android.permission.INTERNET" />\n    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />'
    )
manifest.write_text(m)

# Validate code before changing live scheduling.
run(["python3","-m","py_compile",str(ROOT/"server.py"),str(ROOT/"enhancements.py")])
run(["node","--check",str(ROOT/"public/app.js")])
run(["node","--check",str(ROOT/"public/enhancements.js")])
json.loads(projects_path.read_text())

# Capture protected service PIDs.
protected = [
    "haxlab-analyzer.service","haxlab-ingest.service","haxlab-worker.service",
    "actions.runner.Zennay-Haxlab.vps-bb300bba-haxlab.service",
    "actions.runner.Zennay-Ftmo.vps-bb300bba-ftmo.service",
]
def pid(unit):
    return subprocess.check_output(["systemctl","show",unit,"-p","MainPID","--value"], text=True).strip()
before = {u:pid(u) for u in protected}

# Apply relative priorities live; this does not restart services.
run(["sudo","-n","/usr/local/sbin/zennay-resource-control","apply","all"])

# Build updated Watch APK with the current token.
wear = ROOT / "wear-os"
env = dict(os.environ)
if (ROOT / ".watch-token").exists():
    env["ZENNAY_WATCH_TOKEN"] = (ROOT / ".watch-token").read_text().strip()
watch_build = "skipped-no-gradle"
if (wear / "gradlew").exists():
    run([str(wear/"gradlew"),":app:assembleDebug"], env=env)
    watch_build = "built-wrapper"
elif shutil.which("gradle"):
    run(["gradle","-p",str(wear),":app:assembleDebug"], env=env)
    watch_build = "built-system-gradle"
else:
    candidates = [
        Path("/opt/gradle/bin/gradle"),
        Path("/usr/local/gradle/bin/gradle"),
        Path("/home/ubuntu/gradle/bin/gradle"),
        Path("/home/ubuntu/.local/bin/gradle"),
    ]
    gradle_bin = next((p for p in candidates if p.exists()), None)
    if not gradle_bin:
        root_build = (wear / "build.gradle.kts").read_text()
        agp = re.search(r'com\\.android\\.application"\\) version "([0-9.]+)"', root_build)
        major = int((agp.group(1) if agp else "8").split(".")[0])
        gradle_version = "9.1.0" if major >= 9 else "8.13"
        cache = Path("/home/ubuntu/.cache/zennay-gradle")
        gradle_home = cache / ("gradle-" + gradle_version)
        gradle_bin = gradle_home / "bin/gradle"
        if not gradle_bin.exists():
            cache.mkdir(parents=True, exist_ok=True)
            archive = cache / ("gradle-" + gradle_version + "-bin.zip")
            if not archive.exists():
                urllib.request.urlretrieve(
                    "https://services.gradle.org/distributions/gradle-" + gradle_version + "-bin.zip",
                    archive
                )
            with zipfile.ZipFile(archive) as z:
                z.extractall(cache)
        if not gradle_bin.exists():
            raise RuntimeError("Gradle bootstrap failed")
        watch_build = "built-bootstrapped-gradle-" + gradle_version
    else:
        watch_build = "built-found-gradle"
    run([str(gradle_bin),"-p",str(wear),":app:assembleDebug"], env=env)

# Restart only the dashboard backend.
run(["sudo","systemctl","restart","zennay-cloud.service"])
time.sleep(3)
after = {u:pid(u) for u in protected}
if before != after:
    raise RuntimeError("Protected service PIDs changed: %r -> %r" % (before, after))

def get(path, token=None):
    req = urllib.request.Request("http://127.0.0.1:8765" + path)
    if token:
        req.add_header("Authorization","Bearer " + token)
    with urllib.request.urlopen(req, timeout=8) as response:
        return json.load(response)

token = (ROOT / ".watch-token").read_text().strip() if (ROOT / ".watch-token").exists() else ""
status = get("/api/status")
resources = get("/api/resources")
alerts = get("/api/alerts")
watch = get("/api/v1/watch", token)
watch_alerts = get("/api/v1/alerts", token)

apk_dir = ROOT / "wear-os/app/build/outputs/apk/debug"
apks = list(apk_dir.glob("*.apk"))
summary = {
    "projects": [
        {
            "id": p["id"],
            "progress": p["progress"],
            "status": p.get("status"),
            "resource": p.get("resource"),
            "quality": p.get("quality"),
            "milestones": [{"title":m["title"],"progress":m.get("progress")} for m in p.get("milestones",[])],
        }
        for p in status["projects"]
    ],
    "archived": [p["id"] for p in status.get("archived_projects",[])],
    "resources": resources,
    "alerts": alerts[:5],
    "watch_projects": [p["id"] for p in watch["projects"]],
    "watch_alert_count": len(watch_alerts.get("alerts", [])),
    "protected_pids_unchanged": before == after,
    "cloud_active": subprocess.check_output(["systemctl","is-active","zennay-cloud.service"], text=True).strip(),
    "watch_apk": str(apks[0]) if apks else None,
    "watch_build": watch_build,
}
print("ZENNAY_PATCH_RESULT=" + json.dumps(summary, ensure_ascii=False))

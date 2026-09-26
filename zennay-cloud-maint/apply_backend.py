from pathlib import Path
import json, re

ROOT=Path("/home/ubuntu/zennay-cloud")

def rep(text, old, new, label):
    if new in text:
        return text
    if old not in text:
        raise RuntimeError("missing anchor: "+label)
    return text.replace(old,new,1)

pf=ROOT/"projects.json"
projects=json.loads(pf.read_text())
updates={
"haxlab":{"phase":"Live champion validation · competitive behavior","status":"active","priority":"low","next_step":"Verbeter kickgedrag en competitieve sandbox-resultaten; valideer daarna tegen echte spelers.","progress_basis":"Bouwvoortgang is het gemiddelde van milestone-percentages. Modelkwaliteit staat apart.","milestone_revision":"v3-granular-quality","progresses":[100,100,100,100,100,0,45,55]},
"ftmo":{"phase":"Generation 10 candidate · awaiting walk-forward","status":"active","priority":"high","next_step":"Voer de frozen walk-forward uit voor de Generation-10 candidate; houd development en validatie strikt gescheiden.","progress_basis":"Bouwvoortgang is het gemiddelde van milestone-percentages. Tradingkwaliteit staat apart.","milestone_revision":"v3-granular-quality","progresses":[100,100,100,100,100,100,82,35]},
"supa":{"status":"active","priority":"normal","progress_basis":"Bouwvoortgang is het gemiddelde van milestone-percentages.","milestone_revision":"supa-v3-granular","progresses":[100,100,100,100,0,0,0,0]},
"cloud":{"status":"archived","priority":"system","phase":"Archived · control room operational","progress_basis":"Zennay Cloud is operationeel en gearchiveerd als infrastructuur/control room.","milestone_revision":"v4-archived","progresses":[100,100,100,100,100,100,100]}}
for p in projects:
    u=updates.get(p["id"])
    if not u: continue
    for k,v in u.items():
        if k!="progresses": p[k]=v
    for m,v in zip(p["milestones"],u["progresses"]):
        m["progress"]=v
        m["done"]=v>=100
pf.write_text(json.dumps(projects,ensure_ascii=False,indent=2)+"\n")

lf=ROOT/"project-layout.json"
layout=json.loads(lf.read_text()) if lf.exists() else {"order":[p["id"] for p in projects],"archived":[]}
ids=[p["id"] for p in projects]
layout["order"]=[x for x in layout.get("order",[]) if x in ids]+[x for x in ids if x not in layout.get("order",[])]
layout["archived"]=list(dict.fromkeys([*layout.get("archived",[]),"cloud"]))
lf.write_text(json.dumps(layout,ensure_ascii=False,indent=2)+"\n")

sf=ROOT/"server.py"
s=sf.read_text()
s=rep(s,"import json, os, sqlite3, subprocess, shutil, threading, time, mimetypes, logging","import json, os, sqlite3, subprocess, shutil, threading, time, mimetypes, logging, re","re import")
s=rep(s,"SUPA_SYNC = 'zennay-supa-sync.timer'\nSERVICES = ","""SUPA_SYNC = 'zennay-supa-sync.timer'
RESOURCE_POLICIES = {
    'haxlab': {'priority':'low','cpu_weight':20,'memory_high_mb':2560,'note':'Low priority · can burst when VPS is idle'},
    'ftmo': {'priority':'high','cpu_weight':100,'memory_high_mb':4096,'note':'High priority · wins CPU share under contention'},
    'supa': {'priority':'normal','cpu_weight':50,'memory_high_mb':None,'note':'Normal priority · no compute worker yet'},
    'cloud': {'priority':'system','cpu_weight':40,'memory_high_mb':None,'note':'Control plane · lightweight'}
}
SERVICES = ""","resource policies")

anchor="def collect():\n    projects = json.loads((ROOT/'projects.json').read_text())"
helpers="""def _load_json(path):
    try:
        p=Path(path)
        return json.loads(p.read_text()), p.stat().st_mtime
    except Exception:
        return None, None

def _pct(v):
    return round(float(v)*100,1) if v is not None else None

def hax_quality():
    path='/var/lib/haxlab/derived/training/elite-player-champion-candidate/pipeline-summary.json'
    d,mtime=_load_json(path)
    if not d:return None
    h=d.get('frozen_holdout') or {}
    gate=d.get('live_test_gate') or {}
    joint=_pct(h.get('joint_accuracy'))
    return {'headline':'Player imitation','headline_value':joint,'unit':'%','stage':'Live-test eligible' if gate.get('eligible_for_live_test') else 'Offline validation','source_time':datetime.fromtimestamp(mtime,timezone.utc).isoformat() if mtime else None,'event_key':'hax-holdout-'+str(round(joint or 0,1)),'breakthrough':bool(gate.get('eligible_for_live_test')),'metrics':[{'label':'Joint action accuracy','value':joint,'unit':'%'},{'label':'Direction accuracy','value':_pct(h.get('direction_accuracy')),'unit':'%'},{'label':'Kick F1','value':_pct(h.get('kick_f1')),'unit':'%'},{'label':'Holdout samples','value':h.get('samples'),'unit':''}],'note':'Frozen holdout: imitatie van elite-spelers, niet winrate tegen mensen.'}

def _generation_number(value):
    m=re.search(r'(?:alpha-|generation[-_ ]?)(\\d+)',str(value or ''),re.I)
    return int(m.group(1)) if m else 0

def ftmo_quality():
    root=Path('/opt/ftmo-runner/_work/Ftmo/Ftmo/artifacts/research_outcomes')
    reviews=[]
    for path in root.glob('*/development-review-summary.json'):
        d,mtime=_load_json(path)
        if d: reviews.append((_generation_number(d.get('generation_id') or path.parent.name),path,d,mtime))
    reviews.sort(key=lambda x:x[0],reverse=True)
    current=None
    for gen,path,d,mtime in reviews:
        candidate=next((r for r in d.get('reviews',[]) if r.get('outcome')=='candidate' and r.get('selected_variant')),None)
        if not candidate: continue
        matches=[]
        for tp in (path.parent/candidate['experiment_id']/'trials').glob('*.json'):
            td,_=_load_json(tp)
            rr=(td or {}).get('result') or {}
            if rr.get('variant')==candidate.get('selected_variant'): matches.append(rr)
        if matches:
            rr=max(matches,key=lambda x:float(x.get('cost_1_5x_pnl') or -1e9))
            current={'generation':gen,'result':rr,'time':datetime.fromtimestamp(mtime,timezone.utc).isoformat() if mtime else None}
            break
    validated=[]
    for path in root.glob('*/final-holdout-summary.json'):
        d,_=_load_json(path)
        if not d: continue
        gen=_generation_number(d.get('generation_id') or path.parent.name)
        for dec in d.get('decisions',[]):
            if dec.get('holdout_outcome')!='pass': continue
            rd,_=_load_json(path.parent/dec.get('experiment_id','')/'holdout-run.json')
            validated.append((gen,(rd or {}).get('result') or dec))
    validated.sort(key=lambda x:x[0],reverse=True)
    metrics=[]; stage='Research'; headline=None; source_time=None; event_key='ftmo-research'; breakthrough=False
    if current:
        rr=current['result']; headline=round(float(rr.get('cost_1_5x_pnl') or 0)/0.0001,1)
        stage='Gen '+str(current['generation'])+' candidate · walk-forward pending'
        source_time=current['time']; event_key='ftmo-g'+str(current['generation'])+'-candidate'; breakthrough=True
        metrics += [{'label':'Dev PnL @1.5× costs','value':headline,'unit':' pips'},{'label':'Dev win rate','value':round(float(rr.get('win_rate') or 0)*100,1),'unit':'%'},{'label':'Dev trades','value':rr.get('closed_trades'),'unit':''}]
    if validated:
        gen,rr=validated[0]
        metrics += [{'label':'Validated G'+str(gen)+' holdout','value':round(float(rr.get('total_pnl') or 0)/0.0001,1),'unit':' pips'},{'label':'Holdout win rate','value':round(float(rr.get('win_rate') or 0)*100,1) if rr.get('win_rate') is not None else None,'unit':'%'},{'label':'Holdout trades','value':rr.get('closed_trades'),'unit':''}]
    return {'headline':'Best candidate @1.5× costs','headline_value':headline,'unit':' pips','stage':stage,'source_time':source_time,'event_key':event_key,'breakthrough':breakthrough,'metrics':[m for m in metrics if m.get('value') is not None],'note':'Development is not live-profit proof. Holdout figures are shown separately.'}

def resource_policy(pid):
    return RESOURCE_POLICIES.get(pid)

def collect():
    projects = json.loads((ROOT/'projects.json').read_text())"""
s=rep(s,anchor,helpers,"quality helpers")

old="""        p['progress']=round(sum(m['done'] for m in p['milestones'])/len(p['milestones'])*100)
        p['completed']=sum(m['done'] for m in p['milestones'])
        p['next']=next((m['title'] for m in p['milestones'] if not m['done']), 'Alle milestones afgerond')"""
new="""        for m in p['milestones']:
            m['progress']=round(float(m.get('progress',100 if m.get('done') else 0)))
            m['done']=m['progress']>=100
        p['progress']=round(sum(m['progress'] for m in p['milestones'])/max(1,len(p['milestones'])))
        p['completed']=sum(m['progress']>=100 for m in p['milestones'])
        p['next']=next((m['title'] for m in p['milestones'] if m['progress']<100), 'Alle milestones afgerond')
        p['resource_policy']=resource_policy(p['id'])
        p['quality']=hax_quality() if p['id']=='haxlab' else ftmo_quality() if p['id']=='ftmo' else None"""
s=rep(s,old,new,"granular progress")

anchor="def watch_warnings(data):\n    warnings=[]"
alerts="""def important_alerts(data):
    alerts=[]
    visible,_,_=project_views(data)
    for p in visible:
        for svc in [x for x in p.get('services',[]) if x.get('state') not in ('active','activating','waiting')]:
            alerts.append({'id':"health:"+p['id']+":"+str(svc.get('name'))+":"+str(svc.get('state')),'severity':'needs_attention','project':p['id'],'title':p['name']+' needs attention','message':str(svc.get('name'))+' is '+str(svc.get('state')),'occurred_at':data['time']})
        q=p.get('quality') or {}
        if q.get('breakthrough') and q.get('event_key'):
            alerts.append({'id':'breakthrough:'+q['event_key'],'severity':'breakthrough','project':p['id'],'title':p['name']+' breakthrough','message':str(q.get('headline'))+': '+str(q.get('headline_value'))+str(q.get('unit',''))+' · '+str(q.get('stage','')),'occurred_at':q.get('source_time') or data['time']})
    rs=runner_status()
    if rs.get('state') in ('stale','offline'):
        alerts.append({'id':'runner:'+rs.get('state','offline'),'severity':'needs_attention','project':None,'title':'ChatGPT runner '+rs.get('state','offline'),'message':'Automation heartbeat is not live. Check the runner if projects should be progressing.','occurred_at':data['time']})
    return alerts

def watch_warnings(data):
    warnings=[]"""
s=rep(s,anchor,alerts,"alerts")
s=rep(s,"        'warnings':warnings,\n        'chatgpt_runner':runner,","        'warnings':warnings,\n        'alerts':important_alerts(data),\n        'chatgpt_runner':runner,","watch alerts")
s=rep(s,"            'last_chatgpt_run':p.get('last_chatgpt_run'),\n            'recent_activity':watch_activity(pid,8),","            'last_chatgpt_run':p.get('last_chatgpt_run'),\n            'quality':p.get('quality'),\n            'resource_policy':p.get('resource_policy'),\n            'milestones':p.get('milestones',[]),\n            'recent_activity':watch_activity(pid,8),","detail extras")
sf.write_text(s)
print("backend patched")

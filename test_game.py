"""Rules tests + an end-to-end bot game through the Flask API.  Run: python test_game.py"""
import json
import threading
import time
import urllib.request

import game as G


class T:
    def __init__(self, i, choice, upgrades=None):
        self.id, self.name, self.score = i, f'V{i}', 0
        self.upgrades = upgrades or []
        self.stats = G.new_stats()
        self.choice = choice
        self.goal = None


def run(teams, event=None, pc=None):
    return G.resolve_round(teams, event, {} if pc is None else pc)


def scores(ts):
    return [t.score for t in ts]


# Harvest
t = [T(0, {'card': 'harvest'}), T(1, {'card': 'harvest'}), T(2, {'card': 'guard'})]
run(t); assert scores(t) == [4, 4, 3]  # lone Guard is last place and collects the bank of 1
t = [T(0, {'card': 'harvest'}), T(1, {'card': 'harvest'}), T(2, {'card': 'harvest'}), T(3, {'card': 'guard'})]
r = run(t); assert scores(t)[:3] == [0, 0, 0] and r['overfished']

# Trade
t = [T(0, {'card': 'trade', 'target': 1}), T(1, {'card': 'trade', 'target': 0}), T(2, {'card': 'trade', 'target': 0})]
run(t); assert scores(t) == [4, 4, -1]

# Trade limit: third trade between the same pair fails
pc = {}
for _ in range(2):
    t = [T(0, {'card': 'trade', 'target': 1}), T(1, {'card': 'trade', 'target': 0}), T(2, {'card': 'guard'})]
    run(t, None, pc); assert t[0].score == 4
t = [T(0, {'card': 'trade', 'target': 1}), T(1, {'card': 'trade', 'target': 0}), T(2, {'card': 'guard'})]
run(t, None, pc); assert t[0].score == -1
# Trade Charter caps the pair at 1 and failed trades cost 0
pc = {}
t = [T(0, {'card': 'trade', 'target': 1}, ['charter']), T(1, {'card': 'trade', 'target': 0}), T(2, {'card': 'guard'})]
run(t, None, pc); assert t[0].score == 5
t = [T(0, {'card': 'trade', 'target': 1}, ['charter']), T(1, {'card': 'trade', 'target': 0}), T(2, {'card': 'guard'})]
run(t, None, pc); assert t[0].score == 0

# Events
t = [T(0, {'card': 'harvest'}), T(1, {'card': 'guard'}), T(2, {'card': 'guard'})]
run(t, 'lowwater'); assert t[0].score == 6
t = [T(0, {'card': 'harvest'}), T(1, {'card': 'harvest'}), T(2, {'card': 'guard'})]
run(t, 'lowwater'); assert t[0].score == 0
t = [T(0, {'card': 'trade', 'target': 1}), T(1, {'card': 'trade', 'target': 0}), T(2, {'card': 'harvest'})]
run(t, 'festival'); assert scores(t) == [6, 6, 3]

# Raids
t = [T(0, {'card': 'raid', 'target': 1}), T(1, {'card': 'harvest'}), T(2, {'card': 'guard'})]
run(t); assert t[0].score == 2 and t[1].score == 2 and t[0].stats['raidsUsed'] == 1
t = [T(0, {'card': 'raid', 'target': 1}), T(1, {'card': 'harvest'}, ['watchtower']), T(2, {'card': 'guard'})]
run(t); assert t[1].score == 4 and t[0].score == 1  # repelled; raider is last and collects the bank
t = [T(0, {'card': 'raid', 'target': 2}), T(1, {'card': 'harvest'}), T(2, {'card': 'guard'})]
run(t, 'pirate'); assert t[0].score == 3  # no cost against Guard on Pirate Night
t = [T(0, {'card': 'raid', 'target': 1}, ['fleet']), T(1, {'card': 'guard'}), T(2, {'card': 'harvest'})]
r = run(t); assert t[0].score == 3 and any('Raided by V0' in n for n in r['notes'][1])
# Raid with no uses left falls back to Guard
t = [T(0, {'card': 'raid', 'target': 1}), T(1, {'card': 'harvest'}), T(2, {'card': 'harvest'})]
t[0].stats['raidsUsed'] = 2
r = run(t); assert r['plays'][0]['card'] == 'guard'

# Bank: capped at 3, split on ties
t = [T(i, {'card': 'guard'}) for i in range(5)]
r = run(t); assert r['bank'] == 3
t = [T(0, {'card': 'guard'}), T(1, {'card': 'guard'}), T(2, {'card': 'harvest'}), T(3, {'card': 'harvest'}), T(4, {'card': 'harvest'})]
r = run(t); assert r['bank'] == 2 and scores(t) == [2, 2, 0, 0, 0]

# Bluffs
t = [T(0, {'card': 'harvest'}), T(1, {'card': 'guard'}), T(2, {'card': 'guard'})]
r = run(t); before = scores(t)
G.apply_bluffs(r, [{'teamId': 0, 'claim': 'guard', 'votes': {1: True, 2: False}}], t)
assert t[0].score == before[0] + 1 and t[2].score == before[2] + 1 and t[1].score == before[1]

# Goals
g = T(0, None); g.goal = 'pure_fisher'; g.stats['safeHarvest'] = 3; assert G.goal_met(g)
g.goal = 'safe_harbor'; g.stats['guardRounds'] = 3; assert G.goal_met(g); g.stats['raidsUsed'] = 1; assert not G.goal_met(g)
g.goal = 'sharp_raider'; g.stats.update(raidsUsed=2, raidsClean=2); assert G.goal_met(g)
print('rules tests passed')

# ---------------------------------------------------------------- end to end
import app as A

A.cfg.update(talkSeconds=10, chooseSeconds=15, revealSeconds=5, minRounds=3, maxRounds=3)
c = A.app.test_client()
post = lambda p, b: c.post(p, json=b).get_json()
get = lambda p: c.get(p).get_json()

a = post('/api/join', {'name': 'Alice', 'teamId': 0})
b = post('/api/join', {'name': 'Bob', 'teamId': 1})
assert a['pid'] and b['pid']
assert post('/api/host', {'action': 'start'}).get('error'), 'cannot start with 2 villages'
post('/api/host', {'action': 'fillBots'})
assert post('/api/host', {'action': 'start'}).get('ok')
assert get('/api/preview')['phase'] == 'talk'
assert post('/api/act', {'pid': a['pid'], 'type': 'chat', 'text': 'hello lake'}).get('ok')
assert post('/api/act', {'pid': a['pid'], 'type': 'pm_open', 'with': 1}).get('ok')
assert post('/api/act', {'pid': b['pid'], 'type': 'pm_msg', 'sid': 1, 'text': 'psst'}).get('ok')
assert post('/api/act', {'pid': a['pid'], 'type': 'announce', 'claim': 'guard'}).get('ok')
assert post('/api/act', {'pid': b['pid'], 'type': 'vote', 'annId': 1, 'value': True}).get('ok')
assert post('/api/act', {'pid': b['pid'], 'type': 'choose', 'card': 'guard'}).get('error'), 'no choosing in talk'
assert post('/api/join', {'name': 'Carl', 'teamId': 0}).get('pid')  # joins Alice's village
cap = post('/api/act', {'pid': a['pid'], 'type': 'captain'})
for rnd in range(1, 4):
    assert post('/api/host', {'action': 'endTalk'}).get('ok')
    assert post('/api/act', {'pid': a['pid'], 'type': 'choose', 'card': 'guard'}).get('ok')
    assert post('/api/act', {'pid': b['pid'], 'type': 'choose', 'card': 'trade', 'target': 0}).get('ok')
    assert post('/api/host', {'action': 'forceReveal'}).get('ok')
    st = get('/api/preview')
    assert st['phase'] == 'reveal' and st['lastResult']['round'] == rnd
    assert post('/api/host', {'action': 'next'}).get('ok')
st = get('/api/preview')
assert st['phase'] == 'over' and st['final']['finalRound'] == 3
print('end-to-end game passed. Final ranking:', ' '.join(f"{r['id']}:{r['total']}" for r in st['final']['ranking']))

# ---------------------------------------------------------------- live SSE on a real port
post('/api/host', {'action': 'reset'})
assert post('/api/host', {'action': 'removeBot', 'teamId': 2}).get('ok')  # bots survive a reset; free village 2
import werkzeug.serving
srv = werkzeug.serving.make_server('127.0.0.1', 0, A.app, threaded=True)
port = srv.server_port
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f'http://127.0.0.1:{port}'
pid = json.loads(urllib.request.urlopen(urllib.request.Request(
    base + '/api/join', data=json.dumps({'name': 'Dee', 'teamId': 2}).encode(),
    headers={'Content-Type': 'application/json'})).read())['pid']
stream = urllib.request.urlopen(base + '/events?pid=' + pid, timeout=5)


def read_event():
    line = stream.readline().decode()
    while not line.startswith('data:'):
        line = stream.readline().decode()
    return json.loads(line[5:])


first = read_event()
assert first['you']['name'] == 'Dee' and first['phase'] == 'lobby'
urllib.request.urlopen(urllib.request.Request(
    base + '/api/host', data=json.dumps({'action': 'removeBot', 'teamId': 3}).encode(), headers={'Content-Type': 'application/json'}))
ev = read_event()
assert first['teams'][3]['bot'] and not ev['teams'][3]['bot'], 'SSE pushed the update live'
print('live SSE push passed')
srv.shutdown()

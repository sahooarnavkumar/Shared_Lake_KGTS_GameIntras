"""The Shared Lake: Flask multiplayer server (HTTP + Server-Sent Events).

Run:  python app.py
Host opens http://localhost:3000/host, players open the LAN URL printed in the terminal.
Uses the same public/index.html as the Node version.
"""
import json
import os
import queue
import random
import re
import secrets
import socket
import threading
import time

from flask import Flask, Response, jsonify, request, send_file

import game as G

PORT = int(os.environ.get('PORT', '3000'))
HOST_ANY = bool(os.environ.get('HOST_ANY'))  # allow /host from any device (testing only)
BASE = os.path.dirname(os.path.abspath(__file__))

VILLAGES = [
    ('Northshore', '#2fb6c0'), ('Pearl Bay', '#f3b135'), ('Reedmouth', '#6fb98f'),
    ('Ironhook', '#9b7fd6'), ('Stillwater', '#5e81bd'), ('Ember Cove', '#e5572e'),
]

cfg = {'minRounds': 8, 'maxRounds': 14, 'talkSeconds': 120, 'chooseSeconds': 60,
       'revealSeconds': 45, 'privSeconds': 60, 'eventChance': 0.35}

LOCK = threading.RLock()
app = Flask(__name__)


def now():
    return int(time.time() * 1000)


class Player:
    def __init__(self, name):
        self.pid = secrets.token_hex(8)
        self.name = name
        self.team_id = None


class Team:
    def __init__(self, tid, name, color):
        self.id, self.name, self.color = tid, name, color
        self.bot = False
        self.members = []
        self.captain = None
        self.reset()

    def reset(self):
        self.score = 0
        self.upgrades = []
        self.tokens = 4
        self.goal = None
        self.stats = G.new_stats()
        self.choice = None
        self.locked = False
        self.bluff_used = False

    @property
    def occupied(self):
        return bool(self.members) or self.bot


players = {}
teams = [Team(i, n, c) for i, (n, c) in enumerate(VILLAGES)]


def blank_game():
    return {'phase': 'lobby', 'round': 0, 'finalRound': None, 'event': None, 'deadline': None,
            'pairCounts': {}, 'anns': [], 'annSeq': 0, 'chat': [], 'sessions': [], 'sessSeq': 0,
            'history': [], 'lastResult': None, 'final': None}


S = blank_game()

# ---------------------------------------------------------------- timers
_timer = None
_bot_timers = []
_gen = 0


def _wrap(fn, gen):
    def run():
        with LOCK:
            if gen != _gen:
                return
            fn()
    return run


def set_timer(ms, fn):
    global _timer
    if _timer:
        _timer.cancel()
    _timer = threading.Timer(ms / 1000.0, _wrap(fn, _gen))
    _timer.daemon = True
    _timer.start()


def clear_timers():
    global _timer, _gen
    _gen += 1  # any callback already firing becomes a no-op
    if _timer:
        _timer.cancel()
        _timer = None
    for t in _bot_timers:
        t.cancel()
    _bot_timers.clear()


def occupied():
    return [t for t in teams if t.occupied]


def human_name(pid):
    p = players.get(pid)
    return p.name if p else None


# ---------------------------------------------------------------- views
def team_public(t):
    reveal = S['phase'] in ('reveal', 'over')
    return {
        'id': t.id, 'name': t.name, 'color': t.color, 'bot': t.bot, 'occupied': t.occupied,
        'score': t.score, 'upgrades': t.upgrades, 'tokens': t.tokens, 'bluffUsed': t.bluff_used,
        'captain': 'Bot' if t.bot else human_name(t.captain),
        'members': [human_name(m) for m in t.members],
        'locked': t.locked if S['phase'] == 'choose' else (reveal and t.occupied),
    }


def public_result(r):
    if not r:
        return None
    plays = {}
    for tid, p in r['plays'].items():
        plays[tid] = {'card': 'hidden', 'target': None} if p['card'] == 'raid' else {'card': p['card'], 'target': p['target']}
    return {'round': r['round'], 'event': r['event'], 'plays': plays, 'delta': r['delta'],
            'harvesters': r['harvesters'], 'overfished': r['overfished'], 'bank': r['bank'],
            'bankTo': r['bankTo'], 'log': r['log'], 'trades': r['trades'], 'bluffs': r['bluffs']}


def view(host=False, pid=None):
    me = players.get(pid) if pid else None
    t = teams[me.team_id] if me else None
    out = {
        'now': now(), 'phase': S['phase'], 'round': S['round'], 'deadline': S['deadline'], 'config': cfg,
        'event': ({'key': S['event'], **G.EVENTS[S['event']]} if S['event'] else None),
        'defs': {'upgrades': G.UPGRADES, 'cost': G.UPGRADE_COST, 'bonus': G.GOAL_BONUS, 'maxRaids': G.MAX_RAIDS},
        'teams': [team_public(x) for x in teams],
        'host': bool(host), 'hostUrls': lan_urls() if host else None,
        'you': ({'pid': me.pid, 'name': me.name, 'teamId': me.team_id, 'isCaptain': t.captain == me.pid} if me else None),
        'chat': S['chat'][-120:],
        'sessionsPublic': [{'id': s['id'], 'a': s['a'], 'b': s['b'], 'expires': s['expires'], 'closed': s['closed']} for s in S['sessions']],
        'history': S['history'],
        'lastResult': public_result(S['lastResult']),
        'final': S['final'],
        'anns': [{'id': a['id'], 'teamId': a['teamId'], 'claim': a['claim'],
                  'myVote': (a['votes'].get(t.id) if t and t.id in a['votes'] else None),
                  'voted': list(a['votes'].keys())} for a in S['anns']],
    }
    if t:
        trade_counts = {o.id: S['pairCounts'].get(G.pair_key(t.id, o.id), 0) for o in teams if o.id != t.id}
        st = t.stats
        lr = S['lastResult']
        out['my'] = {
            'goal': ({'key': t.goal, **G.GOALS[t.goal]} if t.goal else None),
            'choice': t.choice, 'locked': t.locked, 'raidsLeft': G.MAX_RAIDS - st['raidsUsed'],
            'stats': {'safeHarvest': st['safeHarvest'], 'guardRounds': st['guardRounds'], 'raidsUsed': st['raidsUsed'],
                      'bankRounds': st['bankRounds'], 'partners': len(st['partners']), 'fooled': st['fooled']},
            'tradeCounts': trade_counts,
            'notes': (lr['notes'].get(t.id, []) if lr else []),
            'sessions': [s for s in S['sessions'] if t.id in (s['a'], s['b'])],
        }
    return out


# ---------------------------------------------------------------- SSE
clients = []  # {'q': Queue, 'host': bool, 'pid': str|None}


def broadcast():
    with LOCK:
        for c in list(clients):
            payload = json.dumps(view(c['host'], c['pid']))
            try:
                c['q'].put_nowait(payload)
            except queue.Full:
                try:
                    c['q'].get_nowait()
                    c['q'].put_nowait(payload)
                except Exception:
                    pass


# ---------------------------------------------------------------- game flow
def reset_teams_for_game():
    for t in teams:
        t.reset()


def start_game():
    global S
    occ = occupied()
    if S['phase'] != 'lobby':
        return 'Game already running'
    if len(occ) < 3:
        return 'Need at least 3 villages (add bots to fill seats)'
    clear_timers()
    reset_teams_for_game()
    keys = list(G.GOALS.keys())
    random.shuffle(keys)
    for i, t in enumerate(occ):
        t.goal = keys[i % len(keys)]
    lo = max(1, min(cfg['minRounds'], cfg['maxRounds']))
    hi = max(lo, cfg['maxRounds'])
    S = blank_game()
    S['finalRound'] = random.randint(lo, hi)
    start_round()
    return None


def start_round():
    S['round'] += 1
    S['event'] = random.choice(list(G.EVENTS.keys())) if random.random() < cfg['eventChance'] else None
    S['phase'] = 'talk'
    S['anns'] = []
    S['sessions'] = []
    S['lastResult'] = None
    for t in teams:
        t.choice, t.locked = None, False
    S['deadline'] = now() + cfg['talkSeconds'] * 1000
    set_timer(cfg['talkSeconds'] * 1000, end_talk)
    broadcast()


def end_talk():
    if S['phase'] != 'talk':
        return
    S['phase'] = 'choose'
    S['deadline'] = now() + cfg['chooseSeconds'] * 1000
    hard = cfg['chooseSeconds'] * 1000
    set_timer(hard, reveal)
    for t in [x for x in teams if x.bot]:
        delay = random.randint(1500, max(2500, min(12000, hard - 2000))) / 1000.0
        bt = threading.Timer(delay, _wrap(lambda t=t: bot_choose(t), _gen))
        bt.daemon = True
        bt.start()
        _bot_timers.append(bt)
    broadcast()


def bot_choose(t):
    if S['phase'] != 'choose' or t.locked:
        return
    others = [o for o in occupied() if o.id != t.id]
    raid_ok = G.MAX_RAIDS - t.stats['raidsUsed'] > 0
    r = random.random()
    if r < 0.28:
        choice = {'card': 'harvest', 'target': None}
    elif r < 0.55:
        choice = {'card': 'trade', 'target': random.choice(others).id}
    elif r < 0.9 or not raid_ok:
        choice = {'card': 'guard', 'target': None}
    else:
        choice = {'card': 'raid', 'target': random.choice(others).id}
    t.choice, t.locked = choice, True
    try_reveal()
    broadcast()


def try_reveal():
    if S['phase'] == 'choose' and all(t.locked for t in occupied()):
        reveal()


def reveal():
    if S['phase'] not in ('choose', 'talk'):
        return
    clear_timers()
    occ = occupied()
    r = G.resolve_round(occ, S['event'], S['pairCounts'])
    G.apply_bluffs(r, S['anns'], occ)
    r['round'] = S['round']
    S['lastResult'] = r
    S['history'].append({'round': S['round'], 'event': S['event'],
                         'scores': {t.id: t.score for t in occ}, 'delta': r['delta']})
    S['phase'] = 'reveal'
    S['deadline'] = now() + cfg['revealSeconds'] * 1000
    set_timer(cfg['revealSeconds'] * 1000, next_round)
    broadcast()


def next_round():
    if S['phase'] != 'reveal':
        return
    if S['round'] >= S['finalRound']:
        end_game()
    else:
        start_round()


def end_game():
    clear_timers()
    ranking = []
    for t in occupied():
        met = G.goal_met(t)
        base = t.score
        if met:
            t.score += G.GOAL_BONUS
        ranking.append({'id': t.id, 'base': base, 'goal': t.goal, 'goalName': G.GOALS[t.goal]['name'],
                        'goalDesc': G.GOALS[t.goal]['desc'], 'met': met, 'total': t.score})
    ranking.sort(key=lambda r: -r['total'])
    top = ranking[0]['total']
    S['final'] = {'finalRound': S['finalRound'], 'ranking': ranking,
                  'winners': [r['id'] for r in ranking if r['total'] == top]}
    S['phase'] = 'over'
    S['deadline'] = None
    broadcast()


def reset_to_lobby():
    global S
    clear_timers()
    reset_teams_for_game()
    S = blank_game()
    broadcast()


# ---------------------------------------------------------------- actions
def move_to(p, team_id):
    if p.team_id is not None:
        old = teams[p.team_id]
        old.members = [m for m in old.members if m != p.pid]
        if old.captain == p.pid:
            old.captain = old.members[0] if old.members else None
    p.team_id = team_id
    t = teams[team_id]
    t.members.append(p.pid)
    if not t.captain:
        t.captain = p.pid


def to_int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def join(body):
    name = str(body.get('name') or '').strip()[:20]
    team_id = to_int(body.get('teamId'))
    if not name:
        return {'error': 'Enter a name'}
    if team_id is None or not (0 <= team_id < len(teams)):
        return {'error': 'Pick a village'}
    if teams[team_id].bot:
        return {'error': 'That village is run by a bot'}
    p = players.get(body.get('pid')) if body.get('pid') else None
    if p:
        p.name = name
        if p.team_id != team_id:
            if S['phase'] != 'lobby':
                return {'error': 'You cannot switch villages mid-game'}
            move_to(p, team_id)
    else:
        p = Player(name)
        players[p.pid] = p
        move_to(p, team_id)
    broadcast()
    return {'pid': p.pid}


def sanitize(s, n):
    return re.sub(r'[\x00-\x1f]', ' ', str(s or '')).strip()[:n]


def act(body):
    p = players.get(body.get('pid'))
    if not p:
        return {'error': 'Unknown player. Rejoin the game.'}
    t = teams[p.team_id]
    is_cap = t.captain == p.pid
    ph = S['phase']
    live = ph in ('talk', 'choose')
    typ = body.get('type')

    if typ == 'captain':
        if t.locked and ph == 'choose':
            return {'error': 'Card already locked'}
        t.captain = p.pid
    elif typ == 'chat':
        text = sanitize(body.get('text'), 300)
        if not text:
            return {'error': 'Empty message'}
        S['chat'].append({'ts': now(), 'team': t.id, 'name': p.name, 'text': text})
        if len(S['chat']) > 300:
            S['chat'].pop(0)
    elif typ == 'pm_open':
        if ph != 'talk':
            return {'error': 'Private talks can only start during the Talk phase'}
        oid = to_int(body.get('with'))
        o = teams[oid] if oid is not None and 0 <= oid < len(teams) else None
        if not o or o.id == t.id or not o.occupied:
            return {'error': 'Pick another village'}
        if o.bot:
            return {'error': 'Bots do not chat'}
        if t.tokens < 1:
            return {'error': 'You have no private-conversation tokens left'}
        if o.tokens < 1:
            return {'error': f'{o.name} has no tokens left'}
        busy = any((not s['closed']) and s['expires'] > now() and (s['a'] in (t.id, o.id) or s['b'] in (t.id, o.id))
                   for s in S['sessions'])
        if busy:
            return {'error': 'One of you is already in a private talk'}
        t.tokens -= 1
        o.tokens -= 1
        S['sessSeq'] += 1
        S['sessions'].append({'id': S['sessSeq'], 'a': t.id, 'b': o.id,
                              'expires': now() + cfg['privSeconds'] * 1000, 'closed': False, 'msgs': []})
    elif typ == 'pm_msg':
        s = next((x for x in S['sessions'] if x['id'] == to_int(body.get('sid'))), None)
        if not s or t.id not in (s['a'], s['b']):
            return {'error': 'No such conversation'}
        if s['closed'] or s['expires'] <= now():
            return {'error': 'That private talk has ended'}
        text = sanitize(body.get('text'), 300)
        if not text:
            return {'error': 'Empty message'}
        s['msgs'].append({'ts': now(), 'team': t.id, 'name': p.name, 'text': text})
    elif typ == 'pm_close':
        s = next((x for x in S['sessions'] if x['id'] == to_int(body.get('sid'))), None)
        if s and t.id in (s['a'], s['b']):
            s['closed'] = True
    elif typ == 'choose':
        if ph != 'choose':
            return {'error': 'You can only lock a card during the Choose phase'}
        if not is_cap:
            return {'error': 'Only the captain can lock the card'}
        if t.locked:
            return {'error': 'Already locked'}
        card = body.get('card')
        if card not in G.CARDS:
            return {'error': 'Bad card'}
        target = to_int(body.get('target')) if body.get('target') is not None else None
        if card == 'raid' and G.MAX_RAIDS - t.stats['raidsUsed'] < 1:
            return {'error': 'No Raids left'}
        if card in ('trade', 'raid'):
            o = teams[target] if target is not None and 0 <= target < len(teams) else None
            if not o or o.id == t.id or not o.occupied:
                return {'error': 'Pick a target village'}
            if card == 'trade' and S['pairCounts'].get(G.pair_key(t.id, o.id), 0) >= G.trade_limit(t, o):
                return {'error': 'Trade limit with that village is used up'}
        else:
            target = None
        t.choice = {'card': card, 'target': target}
        t.locked = True
        broadcast()
        try_reveal()
        return {'ok': True}
    elif typ == 'buy':
        if ph not in ('talk', 'reveal'):
            return {'error': 'Upgrades can be bought during Talk or after a Reveal'}
        if not is_cap:
            return {'error': 'Only the captain can buy upgrades'}
        u = body.get('upgrade')
        if u not in G.UPGRADES:
            return {'error': 'Unknown upgrade'}
        if u in t.upgrades:
            return {'error': 'You already own that'}
        if t.score < G.UPGRADE_COST:
            return {'error': f'You need {G.UPGRADE_COST} points'}
        t.score -= G.UPGRADE_COST
        t.upgrades.append(u)
    elif typ == 'announce':
        if not live:
            return {'error': 'Announce during Talk or Choose'}
        if not is_cap:
            return {'error': 'Only the captain can announce'}
        if t.bluff_used:
            return {'error': 'You already used your announcement'}
        if t.locked:
            return {'error': 'You already locked your card'}
        if body.get('claim') not in G.CARDS:
            return {'error': 'Bad claim'}
        t.bluff_used = True
        S['annSeq'] += 1
        S['anns'].append({'id': S['annSeq'], 'teamId': t.id, 'claim': body['claim'], 'votes': {}})
    elif typ == 'vote':
        if not live:
            return {'error': 'Voting is closed'}
        a = next((x for x in S['anns'] if x['id'] == to_int(body.get('annId'))), None)
        if not a:
            return {'error': 'No such announcement'}
        if a['teamId'] == t.id:
            return {'error': 'You cannot vote on your own announcement'}
        a['votes'][t.id] = bool(body.get('value'))
    else:
        return {'error': 'Unknown action'}
    broadcast()
    return {'ok': True}


def host_act(body):
    a = body.get('action')
    if a == 'config':
        def n(v, lo, hi, d):
            try:
                v = float(v)
            except (TypeError, ValueError):
                return d
            return min(hi, max(lo, v))
        cfg['minRounds'] = round(n(body.get('minRounds'), 1, 60, cfg['minRounds']))
        cfg['maxRounds'] = round(n(body.get('maxRounds'), 1, 60, cfg['maxRounds']))
        if cfg['maxRounds'] < cfg['minRounds']:
            cfg['maxRounds'] = cfg['minRounds']
        cfg['talkSeconds'] = round(n(body.get('talkSeconds'), 10, 900, cfg['talkSeconds']))
        cfg['chooseSeconds'] = round(n(body.get('chooseSeconds'), 10, 600, cfg['chooseSeconds']))
        cfg['revealSeconds'] = round(n(body.get('revealSeconds'), 5, 600, cfg['revealSeconds']))
        cfg['privSeconds'] = round(n(body.get('privSeconds'), 10, 300, cfg['privSeconds']))
        cfg['eventChance'] = n(body.get('eventChance'), 0, 1, cfg['eventChance'])
    elif a == 'addBot':
        tid = to_int(body.get('teamId'))
        if tid is None or not (0 <= tid < len(teams)) or teams[tid].members or teams[tid].bot:
            return {'error': 'Village is not empty'}
        if S['phase'] != 'lobby':
            return {'error': 'Bots can only be added in the lobby'}
        teams[tid].bot = True
    elif a == 'fillBots':
        if S['phase'] != 'lobby':
            return {'error': 'Only in the lobby'}
        for t in teams:
            if not t.members:
                t.bot = True
    elif a == 'removeBot':
        tid = to_int(body.get('teamId'))
        if tid is None or not (0 <= tid < len(teams)) or not teams[tid].bot or S['phase'] != 'lobby':
            return {'error': 'Cannot remove'}
        teams[tid].bot = False
    elif a == 'start':
        e = start_game()
        return {'error': e} if e else {'ok': True}
    elif a == 'endTalk':
        if S['phase'] != 'talk':
            return {'error': 'Not in Talk'}
        end_talk()
        return {'ok': True}
    elif a == 'forceReveal':
        if S['phase'] != 'choose':
            return {'error': 'Not in Choose'}
        reveal()
        return {'ok': True}
    elif a == 'next':
        if S['phase'] != 'reveal':
            return {'error': 'Not in Reveal'}
        next_round()
        return {'ok': True}
    elif a == 'reset':
        reset_to_lobby()
        return {'ok': True}
    elif a == 'say':
        text = sanitize(body.get('text'), 300)
        if not text:
            return {'error': 'Empty'}
        S['chat'].append({'ts': now(), 'team': -1, 'name': 'Game Master', 'text': text})
    else:
        return {'error': 'Unknown host action'}
    broadcast()
    return {'ok': True}


# ---------------------------------------------------------------- http
def lan_urls():
    ips = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('10.255.255.255', 1))  # no packet is sent; just picks the outgoing interface
        ips.append(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            if ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return [f'http://{ip}:{PORT}' for ip in ips if not ip.startswith('127.')]


def is_local():
    return HOST_ANY or request.remote_addr in ('127.0.0.1', '::1', '::ffff:127.0.0.1')


def body_json():
    return request.get_json(force=True, silent=True) or {}


@app.get('/')
def index():
    r = send_file(os.path.join(BASE, 'public', 'index.html'), mimetype='text/html')
    r.headers['Cache-Control'] = 'no-store'
    return r


@app.get('/host')
def host_page():
    if not is_local():
        return Response(f'The host screen is only available on the computer running the server '
                        f'(use http://localhost:{PORT}/host).', status=403, mimetype='text/plain')
    return index()


@app.get('/events')
def events():
    host = request.args.get('host') == '1'
    pid = request.args.get('pid')
    if host and not is_local():
        return Response(status=403)
    with LOCK:
        if not host and pid not in players:
            return Response(status=404)
        c = {'q': queue.Queue(maxsize=30), 'host': host, 'pid': None if host else pid}
        clients.append(c)
        c['q'].put(json.dumps(view(c['host'], c['pid'])))

    def stream():
        try:
            while True:
                try:
                    yield 'data: ' + c['q'].get(timeout=15) + '\n\n'
                except queue.Empty:
                    yield ': ping\n\n'
        finally:
            with LOCK:
                if c in clients:
                    clients.remove(c)

    return Response(stream(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})


@app.post('/api/join')
def api_join():
    with LOCK:
        return jsonify(join(body_json()))


@app.post('/api/act')
def api_act():
    with LOCK:
        return jsonify(act(body_json()))


@app.post('/api/host')
def api_host():
    if not is_local():
        return jsonify({'error': 'Host only'}), 403
    with LOCK:
        return jsonify(host_act(body_json()))


@app.get('/api/preview')
def api_preview():
    with LOCK:
        return jsonify(view())


@app.get('/api/alive')
def api_alive():
    with LOCK:
        return jsonify({'ok': True, 'pid': request.args.get('pid') in players})


if __name__ == '__main__':
    print('\n  THE SHARED LAKE (Flask) is running\n')
    print(f'  Host screen (this computer):  http://localhost:{PORT}/host')
    urls = lan_urls()
    print('  Players join at:              ' + ('   '.join(urls) if urls else f'http://<this-computer-ip>:{PORT}'))
    print('\n  Everyone must be on the same Wi-Fi / network. Ctrl+C to stop.\n')
    app.run(host='0.0.0.0', port=PORT, threaded=True, debug=False, use_reloader=False)

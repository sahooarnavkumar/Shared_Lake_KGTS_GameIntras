"""Pure rules engine for The Shared Lake (Python port). No I/O so it can be unit-tested."""

CARDS = ['harvest', 'trade', 'guard', 'raid']

GOALS = {
    'pure_fisher': {'name': 'Pure Fisher', 'desc': 'Harvest safely (lake not overfished) in 3 rounds.'},
    'trusted_partner': {'name': 'Trusted Partner', 'desc': 'Complete successful trades with 3 different villages.'},
    'safe_harbor': {'name': 'Safe Harbor', 'desc': 'Play Guard in 3 rounds and never play Raid.'},
    'master_bluffer': {'name': 'Master Bluffer', 'desc': 'Fool at least 3 villages with your one bluff.'},
    'bank_collector': {'name': 'Bank Collector', 'desc': 'Collect the shared Guard bank in 2 rounds.'},
    'sharp_raider': {'name': 'Sharp Raider', 'desc': 'Use both Raids, each on a village that did not Guard.'},
}

UPGRADES = {
    'nets': {'name': 'Reinforced Nets', 'tag': 'Harvest', 'plus': '+1 on every successful Harvest', 'minus': 'Overfished lake costs you 1 point'},
    'charter': {'name': 'Trade Charter', 'tag': 'Trade', 'plus': 'Successful trades pay +1; failed trades cost 0', 'minus': 'Max 1 trading round with the same partner'},
    'watchtower': {'name': 'Watchtower', 'tag': 'Guard', 'plus': 'Guard pays 3, and you are immune to Raids', 'minus': 'You add 2 points to the bank'},
    'fleet': {'name': "Raiders' Fleet", 'tag': 'Raid', 'plus': 'Raids steal 3 and cost nothing if target Guards', 'minus': 'The raided village learns who raided'},
}

EVENTS = {
    'lowwater': {'name': 'Low Water', 'desc': 'Harvest pays 6, but only if exactly 1 group harvests. 2 or more harvesters: all get 0. Guard pays 2 as usual.'},
    'festival': {'name': 'Trade Festival', 'desc': 'Successful trades pay 6 each. Failed trades cost 2. Harvest pays only 3.'},
    'pirate': {'name': 'Pirate Night', 'desc': 'Raids steal 3 and cost nothing against Guard. Guard pays 3, but adds nothing to the bank. Trade and Harvest unchanged.'},
}

UPGRADE_COST = 5
GOAL_BONUS = 6
MAX_RAIDS = 2
MAX_BANK = 3


def new_stats():
    return {'safeHarvest': 0, 'guardRounds': 0, 'raidsUsed': 0, 'raidsClean': 0,
            'bankRounds': 0, 'partners': [], 'fooled': 0}


def pair_key(a, b):
    return f'{a}-{b}' if a < b else f'{b}-{a}'


def trade_limit(a, b):
    return 1 if ('charter' in a.upgrades or 'charter' in b.upgrades) else 2


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def resolve_round(teams, event, pair_counts):
    """Resolve one round. Mutates team.score, team.stats and pair_counts.

    teams: occupied teams with .id .name .score .upgrades .stats .choice ({'card','target'} or None)
    """
    by = {t.id: t for t in teams}
    has = lambda t, u: u in t.upgrades
    low_water = event == 'lowwater'
    festival = event == 'festival'
    pirate = event == 'pirate'

    # Normalise choices (missing/invalid -> Guard)
    plays = {}
    for t in teams:
        ch = t.choice or {}
        card = ch.get('card') if ch.get('card') in CARDS else 'guard'
        target = ch.get('target')
        target = target if (_is_int(target) and target in by and target != t.id) else None
        if card == 'raid' and (t.stats['raidsUsed'] >= MAX_RAIDS or target is None):
            card = 'guard'
        if card not in ('trade', 'raid'):
            target = None
        plays[t.id] = {'card': card, 'target': target}

    delta = {t.id: 0 for t in teams}
    notes = {t.id: [] for t in teams}
    log = []

    def add(tid, n, msg):
        delta[tid] += n
        notes[tid].append(('+' if n >= 0 else '') + str(n) + '  ' + msg)

    # ---- Harvest
    harvesters = [t for t in teams if plays[t.id]['card'] == 'harvest']
    n = len(harvesters)
    safe = n > 0 and (n == 1 if low_water else n <= 2)
    overfished = n > 0 and not safe
    h_pay = 6 if low_water else 3 if festival else 4
    for t in harvesters:
        if safe:
            add(t.id, h_pay, 'Harvest')
            if has(t, 'nets'):
                add(t.id, 1, 'Reinforced Nets')
            t.stats['safeHarvest'] += 1
        else:
            notes[t.id].append(' 0  Harvest (lake overfished)')
            if has(t, 'nets'):
                add(t.id, -1, 'Reinforced Nets (overfished)')
    if n == 0:
        log.append('Nobody harvested. The lake rests.')
    elif safe:
        log.append(f"{n} village{'s' if n > 1 else ''} harvested safely.")
    else:
        log.append(f'{n} villages harvested. The lake was overfished! Harvesters get nothing.')

    # ---- Trade (two passes so a pair is judged on pre-round counts)
    trades = []
    outcome = {}
    for t in teams:
        if plays[t.id]['card'] != 'trade':
            continue
        tgt = plays[t.id]['target']
        p = by.get(tgt) if tgt is not None else None
        ok = p is not None and plays[p.id]['card'] == 'trade' and plays[p.id]['target'] == t.id
        limited = False
        if ok and pair_counts.get(pair_key(t.id, p.id), 0) >= trade_limit(t, p):
            ok, limited = False, True
        outcome[t.id] = (ok, limited, p)
    seen = set()
    for t in teams:
        if t.id not in outcome:
            continue
        ok, limited, p = outcome[t.id]
        if ok:
            add(t.id, 6 if festival else 4, f'Trade with {p.name}')
            if has(t, 'charter'):
                add(t.id, 1, 'Trade Charter')
            if p.id not in t.stats['partners']:
                t.stats['partners'].append(p.id)
            key = pair_key(t.id, p.id)
            if key not in seen:
                seen.add(key)
                pair_counts[key] = pair_counts.get(key, 0) + 1
                trades.append({'a': t.id, 'b': p.id, 'ok': True})
                log.append(f'{t.name} and {p.name} traded successfully.')
        else:
            loss = 0 if has(t, 'charter') else (2 if festival else 1)
            if loss:
                add(t.id, -loss, f'Failed trade (pair limit reached with {p.name})' if limited else 'Failed trade')
            else:
                notes[t.id].append(' 0  Failed trade (Trade Charter protects you)')
            trades.append({'a': t.id, 'b': plays[t.id]['target'], 'ok': False, 'limited': limited})
            log.append(f"{t.name}'s trade offer{' to ' + p.name if p else ''} failed.")

    # ---- Raids
    raids = []
    for r in teams:
        if plays[r.id]['card'] != 'raid':
            continue
        tgt = by[plays[r.id]['target']]
        fleet = has(r, 'fleet')
        strong = fleet or pirate
        amt = 3 if strong else 2
        tcard = plays[tgt.id]['card']
        r.stats['raidsUsed'] += 1
        if tcard != 'guard':
            r.stats['raidsClean'] += 1
        if not has(tgt, 'watchtower'):
            add(r.id, amt, f'Raid on {tgt.name}')
            add(tgt.id, -amt, f'Raided by {r.name}' if fleet else 'Raided by an unknown village')
            raids.append({'from': r.id, 'to': tgt.id, 'success': True, 'amount': amt})
            log.append(f'{tgt.name} was raided!')
        else:
            notes[r.id].append(f' 0  Raid on {tgt.name} repelled by their Watchtower')
            notes[tgt.id].append(' 0  A raid was repelled by your Watchtower')
            raids.append({'from': r.id, 'to': tgt.id, 'success': False, 'amount': 0})
            log.append(f'A raid on {tgt.name} was repelled by the Watchtower.')
        if tcard == 'guard' and not strong:
            add(r.id, -1, 'Raid cost (target played Guard)')

    # ---- Guard
    bank_raw = 0
    for t in teams:
        if plays[t.id]['card'] != 'guard':
            continue
        add(t.id, 3 if (has(t, 'watchtower') or pirate) else 2, 'Guard')
        t.stats['guardRounds'] += 1
        if not pirate:
            bank_raw += 2 if has(t, 'watchtower') else 1
    bank = min(MAX_BANK, bank_raw)

    # ---- Apply deltas, then bank to last place
    for t in teams:
        t.score += delta[t.id]
    bank_to = []
    if bank > 0:
        low = min(t.score for t in teams)
        losers = [t for t in teams if t.score == low]
        share = bank // len(losers)
        if share > 0:
            for t in losers:
                t.score += share
                delta[t.id] += share
                notes[t.id].append(f'+{share}  Guard bank (last place)')
                t.stats['bankRounds'] += 1
                bank_to.append(t.id)
            log.append(f"The Guard bank of {bank} went to {' & '.join(t.name for t in losers)} (last place).")
    elif not pirate:
        log.append('The Guard bank was empty.')

    return {'event': event, 'plays': plays, 'delta': delta, 'notes': notes, 'harvesters': n,
            'overfished': overfished, 'bank': bank, 'bankTo': bank_to, 'trades': trades,
            'raids': raids, 'log': log, 'bluffs': []}


def apply_bluffs(result, anns, teams):
    """Announcer's bluff scoring. anns: [{'teamId', 'claim', 'votes': {teamId: bool}}] (True = 'honest')."""
    by = {t.id: t for t in teams}
    for a in anns:
        ann = by.get(a['teamId'])
        if ann is None:
            continue
        truth = result['plays'][a['teamId']]['card'] == a['claim']
        correct, fooled = [], []
        for vid, val in a['votes'].items():
            vid = int(vid)
            if vid not in by or vid == a['teamId']:
                continue
            (correct if bool(val) == truth else fooled).append(vid)
        for vid in correct:
            by[vid].score += 1
            result['delta'][vid] += 1
            result['notes'][vid].append(f'+1  Correct bluff guess on {ann.name}')
        if fooled:
            ann.score += len(fooled)
            result['delta'][ann.id] += len(fooled)
            result['notes'][ann.id].append(
                f"+{len(fooled)}  Fooled {len(fooled)} village{'s' if len(fooled) > 1 else ''} with your bluff")
        ann.stats['fooled'] += len(fooled)
        result['bluffs'].append({'teamId': a['teamId'], 'claim': a['claim'], 'truth': truth,
                                 'correct': correct, 'fooled': fooled})


def goal_met(t):
    s = t.stats
    g = t.goal
    if g == 'pure_fisher':
        return s['safeHarvest'] >= 3
    if g == 'trusted_partner':
        return len(s['partners']) >= 3
    if g == 'safe_harbor':
        return s['guardRounds'] >= 3 and s['raidsUsed'] == 0
    if g == 'master_bluffer':
        return s['fooled'] >= 3
    if g == 'bank_collector':
        return s['bankRounds'] >= 2
    if g == 'sharp_raider':
        return s['raidsUsed'] >= 2 and s['raidsClean'] >= 2
    return False

"""Parallel-channel misroute probe (window-5): capture the ONION routes
xrebalance submits when a request has a source and a destination channel
to the SAME peer (c12 source, c12b destination, both l1<->l2), by holding
every HTLC at l2 so parts stay pending — listsendpays carries the full
route array only while pending.

Background: w4deep run (artifacts-w4deep-20261109-162036) showed round 1
riding c12->c12b correctly, but rounds 2+3 physically riding l1->l2 via
c12 and straight back l2->l1 via c12 (fees burned, nothing moved) while
being counted as delivered — the split layer should make the real c12
inbound arc unusable, and the mirror should translate to c12b.
"""
import json
import time
from pathlib import Path

from pyln.testing.utils import only_one, wait_for

HOLD = Path(__file__).parent / "plugins" / "hold_fail_htlcs.py"
OUT = Path("/tmp/parallel-routes.json")


def test_parallel_channel_routes(node_factory, bitcoind, plugin_opts):
    l1, l2, l3 = node_factory.line_graph(
        3, wait_for_announce=True,
        opts=[{**plugin_opts, 'fee-base': 0, 'fee-per-satoshi': 0},
              {},
              {}])
    # Second l1->l2 channel: the parallel destination.
    c12b, _ = l1.fundchannel(l2, 500000, announce_channel=True)
    wait_for(lambda: len(l1.rpc.listpeerchannels(l2.info['id'])['channels']) == 2)

    both = sorted(c['short_channel_id'] for c in
                  l1.rpc.listpeerchannels(l2.info['id'])['channels'])
    assert len(both) == 2, both
    c12, c12b = both[0], both[1]
    c31, _ = l3.fundchannel(l1, announce_channel=False)
    wait_for(lambda: 'remote' in only_one(
        l1.rpc.listpeerchannels(l3.info['id'])['channels']).get('updates', {}))

    # Drain c12b so it can receive (l1-funded => l1 side full).
    r = l1.rpc.xrebalance(sources=[c12b], destinations=[c31],
                          amount_msat=200_000_000, maxfee_msat=600_000,
                          label='probe-drain')
    assert r['delivered_msat'] == 200_000_000, r

    balances = {c: ch['to_us_msat'] for ch in
                l1.rpc.listpeerchannels()['channels']
                for c in [ch['short_channel_id']]}

    # Arm the hold AFTER the drain: parts of the probe request will be
    # held pending at l2, exposing their full onion routes.
    l2.rpc.plugin('start', str(HOLD))
    wait_for(lambda: any(p['name'].endswith('hold_fail_htlcs.py')
                         for p in l2.rpc.plugin('list')['plugins']))

    # THE request: source c12 (+c31), destination c12b. l2 holds all.
    import threading
    box = {}

    def run():
        box['r'] = l1.rpc.xrebalance(
            sources=[c12, c31], destinations=[c12b],
            amount_msat=200_000_000, maxfee_msat=600_000,
            part_wait=2, maxrounds=6, label='probe')

    t = threading.Thread(target=run)
    t.start()

    # l2 holds everything; parts go pending. Poll listsendpays for
    # route-bearing pending payments until the request thread finishes
    # or we have routes.
    routes = {}
    deadline = time.time() + 90
    while time.time() < deadline and not routes:
        for p in l1.rpc.listsendpays(status='pending')['payments']:
            routes[p['payment_hash'][:8]] = p
        if routes and not t.is_alive():
            break
        time.sleep(0.3)

    # Dump everything we captured, then fail the held HTLCs so the
    # request can end (parts fail; the response tells its story too).
    OUT.write_text(json.dumps({
        'routes': routes,
        'balances_pre': balances,
    }, indent=1, default=str))
    print("CAPTURED ROUTES:", json.dumps(routes, indent=1))

    import subprocess
    holdlines = subprocess.run(
        ['grep', '-h', 'holding htlc', l2.daemon.prefix + '/log'],
        capture_output=True, text=True).stdout.splitlines()
    print("L2HOLDS:", json.dumps(holdlines, indent=1))

    l2.rpc.call('fail-htlcs')
    t.join(timeout=120)
    assert not t.is_alive(), "request thread stuck"
    r = box['r']
    print("RESPONSE:", json.dumps(
        {k: r.get(k) for k in ('delivered_msat', 'fee_msat', 'pending_msat',
                               'rounds_run', 'stop_reason', 'summary')}, indent=1))
    post = {ch['short_channel_id']: ch['to_us_msat'] for ch in
            l1.rpc.listpeerchannels()['channels']}
    print("DELTAS:", json.dumps(
        {c: post.get(c, 0) - balances.get(c, 0) for c in set(post) | set(balances)},
        indent=1))
    with open('/tmp/parallel-response.json', 'w') as f:
        json.dump(r, f, indent=1, default=str)

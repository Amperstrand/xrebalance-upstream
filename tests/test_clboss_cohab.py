"""Cohabitation cell (#226/#236 caveat): xrebalance AND clboss both
active on one node. clboss = ksedgwic/clboss v0.17.2 (host-built).
Observes: clboss startup/management on regtest, an xrebalance request
under active clboss management, fee retuning, and cross-interference.
"""
import json
import time
from pathlib import Path

from pyln.testing.utils import only_one, wait_for

CLBOSS = '/root/.hermes/cache/scratch/clboss-0.17.2/clboss'
OUT = Path('/tmp/clboss-cohab.json')


def test_clboss_cohabitation(node_factory, bitcoind, plugin_opts):
    l1, l2, l3 = node_factory.line_graph(
        3, wait_for_announce=True,
        opts=[{**plugin_opts,
               'plugin': list(plugin_opts['plugin']) + [CLBOSS],
               'allow-deprecated-apis': True},
              {},
              {}])
    scid_fill, _ = l3.fundchannel(l1, announce_channel=False)
    src = only_one(
        l1.rpc.listpeerchannels(l2.info['id'])['channels'])['short_channel_id']
    wait_for(lambda: 'remote' in only_one(
        l1.rpc.listpeerchannels(l3.info['id'])['channels']).get('updates', {}))

    def fees():
        return {c['short_channel_id']:
                (c.get('fee_base_msat'), c.get('fee_proportional_millionths'))
                for c in l1.rpc.listpeerchannels()['channels']}

    # Let clboss manage for a while; record what it does.
    pre = fees()
    time.sleep(45)
    mid = fees()
    clboss_alive = any(p['name'].endswith('clboss') and p['active']
                       for p in l1.rpc.plugin('list')['plugins'])
    notes = {'clboss_active_after_45s': clboss_alive,
             'fees_pre': pre, 'fees_mid': mid}

    # The request under clboss management.
    b = {c['short_channel_id']: c['to_us_msat'] for c in
         l1.rpc.listpeerchannels()['channels']}
    r = l1.rpc.xrebalance(sources=[src], destinations=[scid_fill],
                          amount_msat=100_000_000, maxfee_msat=400_000,
                          label='cohab')
    time.sleep(20)
    a = {c['short_channel_id']: c['to_us_msat'] for c in
         l1.rpc.listpeerchannels()['channels']}
    pays = l1.rpc.listpays()['pays']
    post_fees = fees()
    notes.update({
        'delivered': r['delivered_msat'], 'fee': r['fee_msat'],
        'deltas': {c: a.get(c, 0) - b.get(c, 0) for c in set(a) | set(b)},
        'fees_post': post_fees,
        'pays_after': [(p.get('label', ''), p['status'],
                        p.get('amount_msat')) for p in pays[-8:]],
    })
    OUT.write_text(json.dumps(notes, indent=1, default=str))
    print("COHAB:", json.dumps(notes, indent=1, default=str))
    assert r['delivered_msat'] == 100_000_000, r

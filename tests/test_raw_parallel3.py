"""Deterministic pyln repro attempt for the CLN parallel-channel hop
misdelivery (card 2026-11-09-cln-parallel-hop-misdelivery): mimic the
docker rig's funding topology — BOTH channels l1-funded, the named
return channel's l2 side filled by a direct payment over it — then fire
[a-out, b-in] self-circles and check per-channel deltas.
"""
import hashlib
import json
import time
from pathlib import Path

from pyln.testing.utils import wait_for

OUT = Path('/tmp/cln-parallel-raw3.json')


def test_raw_parallel3_rig_topology(node_factory, bitcoind):
    l1, l2 = node_factory.line_graph(
        2, wait_for_announce=True,
        opts=[{'fee-base': 0, 'fee-per-satoshi': 0},
              {'fee-base': 1, 'fee-per-satoshi': 1000}])
    # BOTH channels l1-funded, like the rig.
    b_scid, _ = l1.fundchannel(l2, 500000)
    wait_for(lambda: len(l1.rpc.listpeerchannels()['channels']) == 2)
    both = sorted(c['short_channel_id'] for c in
                  l1.rpc.listpeerchannels()['channels'])
    a, b = both
    id1, id2 = l1.info['id'], l2.info['id']
    dir_a = 1 if id1 > id2 else 0
    dir_b = 1 if id2 > id1 else 0

    def bal():
        return {c['short_channel_id']: c['to_us_msat'] for c in
                l1.rpc.listpeerchannels()['channels']}

    # Fill l2's side of b: direct 1-hop payment to l2's invoice over b.
    inv = l2.rpc.invoice(amount_msat=200_000_000, label='fill',
                         description='x')
    l1.rpc.sendpay(
        route=[{'id': id2, 'channel': b, 'direction': dir_b,
                'amount_msat': 200_000_000, 'delay': 40, 'style': 'tlv'}],
        payment_hash=inv['payment_hash'],
        payment_secret=inv['payment_secret'])
    l1.rpc.waitsendpay(inv['payment_hash'])

    results = []
    for i in range(4):
        amt = 1_000_000 + i
        fee = 1000 + amt // 1000 + 1
        pre = bal()
        prehex = f'{i + 10:02x}' * 32
        h = hashlib.sha256(bytes.fromhex(prehex)).hexdigest()
        inv = l1.rpc.invoice(amount_msat=amt, label=f'c{i}',
                             description='x', preimage=prehex)
        try:
            l1.rpc.sendpay(
                route=[{'id': id2, 'channel': a, 'direction': dir_a,
                        'amount_msat': amt + fee, 'delay': 46,
                        'style': 'tlv'},
                       {'id': id1, 'channel': b, 'direction': dir_b,
                        'amount_msat': amt, 'delay': 40, 'style': 'tlv'}],
                payment_hash=h, payment_secret=inv['payment_secret'])
            l1.rpc.waitsendpay(h)
            time.sleep(0.3)
            post = bal()
            results.append({'i': i, 'settled': True,
                            'a': post[a] - pre[a],
                            'b': post[b] - pre[b],
                            'exact': post[b] - pre[b] == amt})
        except Exception as e:
            results.append({'i': i, 'settled': False,
                            'err': str(e)[-140:]})
    OUT.write_text(json.dumps(
        {'a': a, 'b': b, 'results': results}, indent=1))
    print("RAW3:", json.dumps(results, indent=1))

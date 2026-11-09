"""CLN-only parallel-pair misdelivery probe, round 2: mimic the plugin's
exact route shape ([channel-a out, channel-b in] to self) repeatedly —
the xrebalance diagnostic showed CLN honoring the named return channel
on one payment and delivering via the OTHER parallel channel on the
next two, with identical submitted routes.
"""
import hashlib
import json
import time
from pathlib import Path

from pyln.testing.utils import wait_for

OUT = Path('/tmp/cln-parallel-raw2.json')


def test_raw_parallel_misdelivery(node_factory, bitcoind):
    l1, l2 = node_factory.line_graph(
        2, wait_for_announce=True,
        opts=[{'fee-base': 0, 'fee-per-satoshi': 0,
               'cltv-delta': 6},
              {'fee-base': 1, 'fee-per-satoshi': 1000,
               'cltv-delta': 6}])
    # l2 funds b so its side has balance to forward the return leg
    c12b, _ = l2.fundchannel(l1, 500000)
    wait_for(lambda: len(l1.rpc.listpeerchannels()['channels']) == 2)
    both = sorted(c['short_channel_id'] for c in
                  l1.rpc.listpeerchannels()['channels'])
    a, b = both
    # direction bits like the plugin's (out on a dir1, in on b dir0) —
    # compute properly: bit = (from_id > to_id)
    id1, id2 = l1.info['id'], l2.info['id']
    dir_a = 1 if id1 > id2 else 0
    dir_b = 1 if id2 > id1 else 0

    def bal():
        return {c['short_channel_id']: c['to_us_msat'] for c in
                l1.rpc.listpeerchannels()['channels']}

    results = []
    for i in range(6):
        amt = 1_000_000 + i
        fee = 1000 + amt // 1000 + 1
        pre = bal()
        prehex = f'{i:02x}' * 32
        h = hashlib.sha256(bytes.fromhex(prehex)).hexdigest()
        inv = l1.rpc.invoice(amount_msat=amt, label=f'r{i}',
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
            results.append({
                'i': i, 'settled': True,
                'a': post[a] - pre[a], 'b': post[b] - pre[b]})
        except Exception as e:
            results.append({'i': i, 'settled': False,
                            'err': str(e)[-160:]})
    OUT.write_text(json.dumps(
        {'a': a, 'b': b, 'results': results}, indent=1))
    print("RAW2:", json.dumps(results, indent=1))
    # honest accounting: every settled payment must move a by -(amt+fee)
    # and b by +amt exactly.

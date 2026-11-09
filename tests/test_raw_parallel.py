"""CLN-only discriminator: raw sendpay circles over a PARALLEL channel
pair (l1<->l2 twice). If some (channel,direction) combination makes CLN
deliver the return leg over the WRONG parallel channel, the misroute is
a CLN sendpay/onion behavior; if all variants are exact, the plugin's
construction differs and the diff is in its onion bits.
"""
import json
import time
from pyln.testing.utils import only_one, wait_for

from pathlib import Path as _P
OUT = _P('/tmp/cln-parallel-raw.json')
from pathlib import Path
OUT = Path('/tmp/cln-parallel-raw.json')


def test_raw_sendpay_parallel_pair(node_factory, bitcoind):
    l1, l2 = node_factory.line_graph(2, wait_for_announce=True,
                                     opts=[{'fee-base': 0,
                                            'fee-per-satoshi': 0},
                                           {'fee-base': 1,
                                            'fee-per-satoshi': 1000}])
    c12b, _ = l1.fundchannel(l2, 500000)
    wait_for(lambda: len(l1.rpc.listpeerchannels()['channels']) == 2)
    both = sorted(c['short_channel_id'] for c in
                  l1.rpc.listpeerchannels()['channels'])
    a, b = both[0], both[1]

    def bal():
        return {c['short_channel_id']: c['to_us_msat'] for c in
                l1.rpc.listpeerchannels()['channels']}

    results = []
    # Try both direction-bit combinations for the return leg on b.
    for ret_dir in (0, 1):
        pre = bal()
        prehex = f'{ret_dir:02x}' * 32
        h = __import__('hashlib').sha256(bytes.fromhex(prehex)).hexdigest()
        inv = l1.rpc.invoice(amount_msat=10_000_000, label=f'raw{ret_dir}',
                             description='x', preimage=prehex)
        try:
            l1.rpc.sendpay(
                route=[{'id': l2.info['id'], 'channel': a, 'direction': 1,
                        'amount_msat': 10_010_001, 'delay': 60,
                        'style': 'tlv'},
                       {'id': l1.info['id'], 'channel': b,
                        'direction': ret_dir,
                        'amount_msat': 10_000_000, 'delay': 20,
                        'style': 'tlv'}],
                payment_hash=h, payment_secret=inv['payment_secret'])
            l1.rpc.waitsendpay(h)
            time.sleep(0.5)
            post = bal()
            results.append({
                'return_dir': ret_dir, 'ok': True,
                'delta_a': post[a] - pre[a], 'delta_b': post[b] - pre[b]})
        except Exception as e:
            results.append({'return_dir': ret_dir, 'ok': False,
                            'err': str(e)[:200]})
    OUT.write_text(json.dumps(results, indent=1))
    print("RAW RESULTS:", json.dumps(results, indent=1))

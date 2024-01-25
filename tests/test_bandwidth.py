import pylxd
import json
import re

import testnet.testnet as testnet


def test_bandwidth():
    client = pylxd.Client()

    leafs = []
    for n in testnet.get_nodes(client):
        js = json.loads(n.description)
        if js["role"] == "leaf":
            leafs.append(n)

    # start an iperf daemon on each router, and then measure bandwidth for every device combination
    for i in leafs:
        iperf_running = i.execute(["pgrep", "-cf", '"iperf -sD"'])
        if int(iperf_running.stdout) < 1:
            err = i.execute(["iperf", "-sD"])
            if err.exit_code != 0:
                raise RuntimeError(err.stderr)

    # measure bandwidth between each node. vm-to-vm traffic should easily be above 50gbps.
    # less than 50 indicates an issue; bridge.mtu 6666 for example causes this to fall as low as 121 Mbits/sec.
    for i in leafs:
        for j in leafs:
            if j != i:
                err = i.execute(
                    [
                        "iperf",
                        "-c",
                        j.state().network["lo"]["addresses"][1]["address"],
                        "-t1",
                        "-P2",
                        "-t0.1",
                    ]
                )
                print("iperf: " + i.name + " -> " + j.name)
                print(err.stdout)
                if err.exit_code != 0:
                    print("iperf: " + i.name + " -> " + j.name + " failed")
                    raise RuntimeError(err.stderr)
                elif "tcp connect failed" in err.stderr:
                    raise RuntimeError(err.stderr)
                regex = re.compile(
                    r"^\[SUM\].* ([0-9]{1,3}\.?[0-9]?) Gbits\/sec", re.MULTILINE
                )
                gigabits = regex.findall(err.stdout)
                if len(gigabits) == 0:
                    raise ValueError(
                        "error fetching iperf output. is the bandwidth < 1 Gbit?"
                    )
                print(
                    "{} has {}gbps throughput to {}".format(i.name, gigabits[0], j.name)
                )
                assert int(float(gigabits[0])) > 25

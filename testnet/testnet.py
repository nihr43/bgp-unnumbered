import logging
import argparse
import uuid
import time
import random
import re
import json
from os import chmod
from Crypto.PublicKey import RSA
from jinja2 import Environment, FileSystemLoader

import ansible_runner
import pylxd


def get_nodes(client):
    """
    find instances
    """
    members = []
    for i in client.instances.all():
        try:
            js = json.loads(i.description)
            if js["bgp-unnumbered"]:
                members.append(i)
        except json.decoder.JSONDecodeError:
            continue

    return members


def cleanup(client, log, pylxd):
    instances_to_delete = get_nodes(client)

    for i in instances_to_delete:
        try:
            i.stop(wait=True)
        except pylxd.exceptions.LXDAPIException as lxdapi_exception:
            if str(lxdapi_exception) == "The instance is already stopped":
                pass
            else:
                raise Exception(lxdapi_exception)
        i.delete(wait=True)
        log.info(i.name + " deleted")

    networks = client.networks.all()
    for n in networks:
        try:
            if n.description == "bgp-unnumbered":
                n.delete(wait=True)
                log.info(n.name + " deleted")
        except pylxd.exceptions.NotFound:
            pass


def create_keypair(RSA):
    """
    creates ssh keypair for use with ansible
    returns public key
    """
    key = RSA.generate(4096)
    with open("./private.key", "wb") as content_file:
        chmod("./private.key", 0o600)
        content_file.write(key.exportKey("PEM"))
    pubkey = key.publickey()
    with open("./public.key", "wb") as content_file:
        content_file.write(pubkey.exportKey("OpenSSH"))
    return pubkey


def create_node(client, role, image, pubkey, log):
    name = "bgp-" + role + "-" + str(uuid.uuid4())[0:5]
    config = {
        "name": name,
        "description": "bgp-unnumbered",
        "source": {
            "type": "image",
            "mode": "pull",
            "server": "https://images.linuxcontainers.org",
            "protocol": "simplestreams",
            "alias": image,
        },
        "config": {"limits.cpu": "2", "limits.memory": "1GB"},
        "type": "virtual-machine",
    }
    log.info("creating node " + name)
    inst = client.instances.create(config, wait=True)
    inst.description = '{"bgp-unnumbered": true, "role": "%s"}' % role
    inst.save(wait=True)
    inst.start(wait=True)
    wait_until_ready(inst, log)

    if "rocky" in image:
        pkgm = "yum"
    elif "debian" or "ubuntu" in image:
        pkgm = "apt"

    err = inst.execute(
        [pkgm, "install", "python3", "openssh-server", "ca-certificates", "-y"]
    )
    log.info(err.stdout)
    if err.exit_code != 0:
        raise RuntimeError(err.stderr)
    err = inst.execute(["mkdir", "-p", "/root/.ssh"])
    log.info(err.stdout)
    if err.exit_code != 0:
        log.info("failed to mkdir /root/.ssh")
        raise RuntimeError(err.stderr)

    inst.files.put("/root/.ssh/authorized_keys", pubkey.exportKey("OpenSSH"))
    # wow! subsequent reboots in network configuration were borking our ssh installation/configuration
    inst.execute(["sync"])
    return inst


def wait_until_ready(instance, log):
    """
    waits until an instance is executable
    """
    log.info("waiting for lxd agent to become ready on " + instance.name)
    count = 30
    for i in range(count):
        try:
            exit_code = instance.execute(["hostname"]).exit_code
        except BrokenPipeError:
            continue

        if exit_code == 0:
            break
        if i == count - 1:
            raise TimeoutError("timed out waiting")
        time.sleep(1)


def create_bridge(client, inst_a, inst_b, log):
    """
    creates an l2 bridge linking two lxd instances
    """
    config = {
        "ipv4.dhcp": "false",
        "ipv4.nat": "false",
        "ipv6.dhcp": "false",
        "ipv6.nat": "false",
        "ipv4.address": "none",
        "ipv6.address": "none",
        "bridge.mtu": "9000",
    }
    name = inst_a.name[-5:] + "-" + inst_b.name[-5:]
    log.info("creating network " + name)
    client.networks.create(name, description="bgp-unnumbered", config=config)
    """
    qemu appears to use this 'eth' number to enumerate the pci ids.
    if 'eth' is undefined or conflicting, the error looks like this:
    pylxd.exceptions.LXDAPIException: Failed to start device "eth5201": Failed setting up device "eth5201": Failed adding NIC device: PCI: slot 0 function 0 not available for virtio-net-pci, in use by virtio-net-pci,id=dev-lxd_eth6961

    we'll punt this issue (sorry) with random ids as follows:
    """
    eth_id = random.randint(10, 9999)
    ethname = "eth" + str(eth_id)
    # lxd manipulates linux bridges on our behalf.
    # for linux bridges to behave as 802.1q trunks, vlan_filtering needs
    # enabled and desired vids need added to the bridge and the taps.
    # vlan.tagged does this for us.  you can check its effect with `bridge vlan show`
    inst_a.devices[ethname] = {
        "name": ethname,
        "network": name,
        "type": "nic",
        "vlan.tagged": "10",
    }
    inst_b.devices[ethname] = {
        "name": ethname,
        "network": name,
        "type": "nic",
        "vlan.tagged": "10",
    }
    inst_a.save(wait=True)
    inst_b.save(wait=True)


def run_tests(client, log):
    log.info("running regression tests")
    routers = [i for i in client.instances.all() if i.description == "bgp-unnumbered"]

    for i in routers:
        log.info(
            "found router {} ip {}".format(
                i.name, i.state().network["lo"]["addresses"][1]["address"]
            )
        )

    # each router should be able to reach every other router via icmp
    for i in routers:
        for j in routers:
            if j != i:
                err = i.execute(
                    [
                        "ping",
                        "-c1",
                        "-W1",
                        j.state().network["lo"]["addresses"][1]["address"],
                    ]
                )
                log.info("icmp: " + i.name + " -> " + j.name)
                if err.exit_code != 0:
                    log.info("icmp: " + i.name + " -> " + j.name + " failed")
                    raise RuntimeError(err.stderr)

    # start an iperf daemon on each router, and then measure bandwidth for every device combination
    for i in routers:
        err = i.execute(["iperf", "-sD"])
        if err.exit_code != 0:
            raise RuntimeError(err.stderr)

    # measure bandwidth between each node. vm-to-vm traffic should easily be above 10gbps.
    # less than 10 indicates an issue; bridge.mtu 6666 for example causes this test to fail
    for i in routers:
        for j in routers:
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
                log.info("iperf: " + i.name + " -> " + j.name)
                log.info(err.stdout)
                if err.exit_code != 0:
                    log.info("iperf: " + i.name + " -> " + j.name + " failed")
                    raise RuntimeError(err.stderr)
                elif "tcp connect failed" in err.stderr:
                    raise RuntimeError(err.stderr)
                regex = re.compile(
                    r"^\[SUM\].* ([0-9]{1,3}\.?[0-9]?) Gbits\/sec", re.MULTILINE
                )
                gigabits = regex.findall(err.stdout)
                if len(gigabits) == 0:
                    raise RuntimeError(
                        "error fetching iperf output. is the bandwidth < 1 Gbit?"
                    )
                if int(float(gigabits[0])) < 10:
                    log.info("iperf " + i.name + " -> " + j.name + " bandwidth failure")
                    raise RuntimeError()

    log.info("all tests passing")


def main():
    logging.basicConfig(format="%(funcName)s(): %(message)s")
    log = logging.getLogger(__name__)
    log.setLevel(logging.INFO)
    client = pylxd.Client()

    parser = argparse.ArgumentParser()
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--cleanup", action="store_true")
    parser.add_argument(
        "--spines",
        "-s",
        type=int,
        default=2,
        help="Number of spines to provision. Defaults to 2.",
    )
    parser.add_argument(
        "--leafs",
        "-l",
        type=int,
        default=3,
        help="Number of leafs to provision. Defaults to 3.",
    )
    parser.add_argument("--image", type=str, default="debian/12")
    parser.add_argument(
        "--run-tests",
        action="store_true",
        help="Run interconnectivity regression tests.",
    )
    args = parser.parse_args()

    if args.cleanup:
        cleanup(client, log, pylxd)

    if args.create:
        pubkey = create_keypair(RSA)

        spines = [
            create_node(client, "spine", args.image, pubkey, log)
            for i in range(args.spines)
        ]
        leafs = [
            create_node(client, "leaf", args.image, pubkey, log)
            for i in range(args.leafs)
        ]

        all_routers = spines + leafs
        for r in all_routers:
            r.stop(wait=True)

        for leaf in leafs:
            for spine in spines:
                create_bridge(client, leaf, spine, log)

        for r in all_routers:
            r.start(wait=True)
            wait_until_ready(r, log)

        env = Environment(loader=FileSystemLoader("templates"))

        template = env.get_template("virtual-inventory.j2")
        with open("virtual.inventory", "w") as inventory:
            inventory.truncate()
            inventory.write(template.render(spines=spines, leafs=leafs))

        ansible_runner.run(
            private_data_dir="./", inventory="virtual.inventory", playbook="testnet.yml"
        )

        log.info("environment created.  follow-up configuration can be performed with:")
        print("ansible-playbook main.yml -i virtual.inventory")

    if args.run_tests:
        run_tests(client, log)


if __name__ == "__main__":
    import sys

    sys.exit(main())

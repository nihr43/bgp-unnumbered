#!/usr/bin/python3

import uuid
import time
import random


def cleanup(client, log, pylxd):
    instances_to_delete = []
    for i in client.instances.all():
        if i.name.startswith('bgp-unnumbered-'):
            log.info('found ' + i.name)
            instances_to_delete.append(i)

    for i in instances_to_delete:
        try:
            i.stop(wait=True)
        except pylxd.exceptions.LXDAPIException:
            pass
        i.delete()
        log.info(i.name + ' deleted')


def create_node(client, name, image, log):
    name = 'bgp-unnumbered-' + name + '-' + str(uuid.uuid4())[0:5]
    config = {'name': name,
              'source': {'type': 'image',
                         'mode': 'pull',
                         'server': 'https://images.linuxcontainers.org',
                         'protocol': 'simplestreams',
                         'alias': image},
              'config': {'limits.cpu': '2',
                         'limits.memory': '1GB'},
              'type': 'virtual-machine'}
    log.info('creating node ' + name)
    inst = client.instances.create(config, wait=True)
    inst.start(wait=True)
    wait_until_ready(inst, log)
    err = inst.execute(['apt', 'install', 'wget', 'python3', 'openssh-server', '-y'])
    if err.exit_code != 0:
        log.info('failed to install openssh-server')
        exit(1)
    err = inst.execute(['mkdir', '-p', '/root/.ssh'])
    if err.exit_code != 0:
        log.info('failed to mkdir /root/.ssh')
        exit(1)
    err = inst.execute(['wget', 'https://github.com/nihr43.keys', '-O', '/root/.ssh/authorized_keys'])
    if err.exit_code != 0:
        log.info('failed to fetch authorized_keys')
        exit(1)
    return inst


def wait_until_ready(instance, log):
    '''
    waits until an instance is executable
    '''
    log.info('waiting for lxd agent to become ready on ' + instance.name)
    count = 30
    for i in range(count):
        if instance.execute(['hostname']).exit_code == 0:
            break
        if i == count-1:
            log.info('timed out waiting')
            exit(1)
        time.sleep(1)


def create_bridge(client, inst_a, inst_b, log):
    '''
    creates an l2 bridge linking two lxd instances
    '''
    config = {'ipv4.dhcp': 'false',
              'ipv4.nat': 'false',
              'ipv6.dhcp': 'false',
              'ipv6.nat': 'false',
              'ipv4.address': 'none',
              'ipv6.address': 'none',
              'bridge.mtu': '9000'}
    name = inst_a.name[-5:] + '-' + inst_b.name[-5:]
    log.info('creating cable ' + name)
    client.networks.create(name, description='bgp-unnumbered', config=config)
    '''
    qemu appears to use this 'eth' number to enumerate the pci ids.
    if 'eth' is undefined or conflicting, the error looks like this:
    pylxd.exceptions.LXDAPIException: Failed to start device "eth5201": Failed setting up device "eth5201": Failed adding NIC device: PCI: slot 0 function 0 not available for virtio-net-pci, in use by virtio-net-pci,id=dev-lxd_eth6961

    we'll punt this issue (sorry) with random ids as follows:
    '''
    eth_id = random.randint(10, 9999)
    ethname = 'eth' + str(eth_id)
    inst_a.devices[ethname] = {'name': ethname, 'network': name, 'type': 'nic'}
    inst_b.devices[ethname] = {'name': ethname, 'network': name, 'type': 'nic'}
    inst_a.save(wait=True)
    inst_b.save(wait=True)


if __name__ == '__main__':
    def privileged_main():
        import pylxd
        import logging
        import argparse

        logging.basicConfig(format='%(funcName)s(): %(message)s')
        log = logging.getLogger(__name__)
        log.setLevel(logging.INFO)
        client = pylxd.Client()

        parser = argparse.ArgumentParser()
        parser.add_argument('--create', action='store_true')
        parser.add_argument('--cleanup', action='store_true')
        parser.add_argument('--spines', '-s', type=int, default=2)
        parser.add_argument('--leafs', '-l', type=int, default=3)
        parser.add_argument('--image', type=str, default='debian/12')
        args = parser.parse_args()

        if args.cleanup:
            cleanup(client, log, pylxd)
        elif args.create:
            spines = [create_node(client, 'spine', args.image, log)
                      for i in range(args.spines)]
            leafs = [create_node(client, 'leaf', args.image, log)
                     for i in range(args.leafs)]
            for i in leafs:
                try:
                    i.stop(wait=True)
                except:
                    pass
                for s in spines:
                    try:
                        s.stop(wait=True)
                    except:
                        pass
                    # create_bridge mutates the state on the remote, so we need to refresh with each iteration
                    leaf = client.instances.get(i.name)
                    spine = client.instances.get(s.name)
                    create_bridge(client, spine, leaf, log)

            for i in spines:
                i.start(wait=True)

            for i in leafs:
                i.start(wait=True)

    privileged_main()

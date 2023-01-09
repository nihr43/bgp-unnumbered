#!/usr/bin/python3

import uuid
import time
import random
from os import chmod


def cleanup(client, log, pylxd):
    instances_to_delete = [i for i in client.instances.all()
                           if i.description == 'bgp-unnumbered']

    for i in instances_to_delete:
        try:
            i.stop(wait=True)
        except pylxd.exceptions.LXDAPIException:
            pass
        i.delete(wait=True)
        log.info(i.name + ' deleted')

    networks = client.networks.all()
    for n in networks:
        try:
            if n.description == 'bgp-unnumbered':
                n.delete(wait=True)
                log.info(n.name + ' deleted')
        except pylxd.exceptions.NotFound:
            pass


def create_keypair(RSA):
    '''
    creates ssh keypair for use with ansible
    returns public key
    '''
    key = RSA.generate(4096)
    with open("./private.key", 'wb') as content_file:
        chmod("./private.key", 0o600)
        content_file.write(key.exportKey('PEM'))
    pubkey = key.publickey()
    with open("./public.key", 'wb') as content_file:
        content_file.write(pubkey.exportKey('OpenSSH'))
    return pubkey


def create_node(client, name, image, pubkey, log):
    name = 'bgp-' + name + '-' + str(uuid.uuid4())[0:5]
    config = {'name': name,
              'description': 'bgp-unnumbered',
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
    err = inst.execute(['apt', 'install', 'python3', 'openssh-server', 'ca-certificates', '-y'])
    log.info(err.stdout)
    if err.exit_code != 0:
        log.info(err.stderr)
        exit(1)
    err = inst.execute(['mkdir', '-p', '/root/.ssh'])
    log.info(err.stdout)
    if err.exit_code != 0:
        log.info('failed to mkdir /root/.ssh')
        log.info(err.stderr)
        exit(1)

    inst.files.put('/root/.ssh/authorized_keys', pubkey.exportKey('OpenSSH'))
    # wow! subsequent reboots in network configuration were borking our ssh installation/configuration
    inst.execute(['sync'])
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
    log.info('creating network ' + name)
    client.networks.create(name, description='bgp-unnumbered', config=config)
    '''
    qemu appears to use this 'eth' number to enumerate the pci ids.
    if 'eth' is undefined or conflicting, the error looks like this:
    pylxd.exceptions.LXDAPIException: Failed to start device "eth5201": Failed setting up device "eth5201": Failed adding NIC device: PCI: slot 0 function 0 not available for virtio-net-pci, in use by virtio-net-pci,id=dev-lxd_eth6961

    we'll punt this issue (sorry) with random ids as follows:
    '''
    eth_id = random.randint(10, 9999)
    ethname = 'eth' + str(eth_id)
    # lxd manipulates linux bridges on our behalf.
    # for linux bridges to behave as 802.1q trunks, vlan_filtering needs
    # enabled and desired vids need added to the bridge and the taps.
    # vlan.tagged does this for us.  you can check its effect with `bridge vlan show`
    inst_a.devices[ethname] = {'name': ethname, 'network': name, 'type': 'nic', 'vlan.tagged': '10'}
    inst_b.devices[ethname] = {'name': ethname, 'network': name, 'type': 'nic', 'vlan.tagged': '10'}
    inst_a.save(wait=True)
    inst_b.save(wait=True)


def run_tests(client, log):
    log.info('running regression tests')
    routers = [i for i in client.instances.all()
               if i.description == 'bgp-unnumbered']

    for i in routers:
        log.info('found router ' + i.name + ' ip ' + i.state().network['lo']['addresses'][1]['address'])

    for i in routers:
        for j in routers:
            err = i.execute(['ping', '-c1', '-W1', j.state().network['lo']['addresses'][1]['address']])
            log.info('recursive ping: ' + i.name + ' -> ' + j.name)
            if err.exit_code != 0:
                log.info('recursive ping: ' + i.name + ' -> ' + j.name + ' failed')
                log.info(err.stderr)
                exit(1)


if __name__ == '__main__':
    def privileged_main():
        import pylxd
        import logging
        import argparse
        import ansible_runner
        from jinja2 import Environment, FileSystemLoader
        from Crypto.PublicKey import RSA

        logging.basicConfig(format='%(funcName)s(): %(message)s')
        log = logging.getLogger(__name__)
        log.setLevel(logging.INFO)
        client = pylxd.Client()

        parser = argparse.ArgumentParser()
        parser.add_argument('--create', action='store_true')
        parser.add_argument('--cleanup', action='store_true')
        parser.add_argument('--spines', '-s', type=int, default=2,
                            help='Number of spines to provision')
        parser.add_argument('--leafs', '-l', type=int, default=3,
                            help='Number of leafs to provision')
        parser.add_argument('--image', type=str, default='debian/12')
        parser.add_argument('--run-tests', action='store_true')
        args = parser.parse_args()

        if args.cleanup:
            cleanup(client, log, pylxd)
        elif args.create:
            pubkey = create_keypair(RSA)

            spines = [create_node(client, 'spine', args.image, pubkey, log)
                      for i in range(args.spines)]
            leafs = [create_node(client, 'leaf', args.image, pubkey, log)
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
                wait_until_ready(i, log)

            for i in leafs:
                i.start(wait=True)
                wait_until_ready(i, log)

            env = Environment(
                loader=FileSystemLoader('templates')
            )

            template = env.get_template('virtual-inventory.j2')
            with open('virtual.inventory', 'w') as inventory:
                inventory.truncate()
                inventory.write(template.render(spines=spines, leafs=leafs))

            ansible_runner.run(
                private_data_dir='./',
                inventory='virtual.inventory',
                playbook='main.yml'
            )

            log.info('environment created.  follow-up configuration can be performed with:')
            print('ansible-playbook main.yml -i virtual.inventory')
        elif args.run_tests:
            run_tests(client, log)

    privileged_main()
#!/usr/bin/python3

import uuid
import time


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
    return inst


def wait_until_ready(instance, log):
    '''
    waits until an instance is executable
    '''
    count = 30
    for i in range(count):
        if instance.execute(['hostname']).exit_code == 0:
            break
        if i == count-1:
            log.info('timed out waiting')
            exit(1)
        log.info('waiting for lxd agent on ' + instance.name)
        time.sleep(2)


if __name__ == '__main__':
    def privileged_main():
        import pylxd
        import logging
        import argparse

        logging.basicConfig(level=logging.INFO,
                            format='%(funcName)s(): %(message)s')
        log = logging.getLogger(__name__)
        log.setLevel(logging.INFO)
        client = pylxd.Client()

        parser = argparse.ArgumentParser()
        parser.add_argument('--create', action='store_true')
        parser.add_argument('--cleanup', action='store_true')
        parser.add_argument('--spines', type=int, default=2)
        parser.add_argument('--leafs', type=int, default=3)
        parser.add_argument('--image', type=str, default='debian/12')
        args = parser.parse_args()

        if args.create:
            for i in range(args.spines):
                create_node(client, 'spine', args.image, log)
            for i in range(args.leafs):
                create_node(client, 'leaf', args.image, log)
        elif args.cleanup:
            cleanup(client, log, pylxd)

    privileged_main()

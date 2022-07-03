# bgp-unnumbered

This is a reference implementation of bgp routing over unnumbered network interfaces using Debian and FRRouting.  The architecture is a clos spine-leaf network, where the leafs are hypervisors running lxd and kubernetes, and the spines are x86 Debian systems with extra pcie network interfaces.

The primary motivation for implementing a pure-L3 network down to the hypervisors is to establish a truly non-blocking, fault tolerant, vendor-agnostic (mostly) datacenter network.  There are very good reasons the entire internet is not a single L2 segment - what we're doing here is essentially pulling that 'edge' down a bit farther.

The [BGP-unnumbered](https://www.oreilly.com/library/view/bgp-in-the/9781491983416/ch04.html) technique makes this very easy to implement and manage, once you've grokked the initial configuration.  The fundamental concept that allows this to work is the advertisement of ipv4 prefixes over ipv6 link-local next-hops.  In the network presented here, moving a cable or introducing a new leaf or spine requires no configuration change (other than adding the individual device to the inventory and provisioning it).  I could walk down to my rack and physically turn my spine-leaf network into a hub-spoke, or a long horrible chain, and bgp would just figure it out - in fact, if only one cable is moved at a time, there would likely be very little service interruption.  Of course, this means physical security is important - and if you were doing this in a real datacenter, you would want to add authentication for bgp peering, and prefix rules / clever firewalling to ensure guest VMs dont somehow poison the datacenter routing table.

From a high-level management aspect, this is an attractive concept because the tools and the process (ansible in this example) to configure the spines/leafs is the same as for the hypervisors.  In this environment, the spine routers are simply "linux boxes with better network cards" ( though of course if i were building a real datacenter, i would substitute these components with whitebox linux ONIE switches).  Taking this a step farther, consider the following: these linux routers have the same upgrade path as the rest of the infrastructure, the same install image, the same monitoring, the same package management...  In my mind, open source whitebox routing is the ultimate evolution ( and perhap irony ) of "software defined networking" and "network automation".

I am far from the first person to build such a thing, though this is sort of a darker magic that is difficult to piece together.  [Vincent Bernat](https://vincent.bernat.ch/en/blog/2017-vxlan-bgp-evpn) has excellent content that helped me figure this out.

## Components

This example uses Debian and FRR.  The basic components follow.

A loopback /32 ip, and one or more unnumbered "up" interfaces:

```
auto lo
iface lo inet loopback

auto lo:10
iface lo:10 inet static
    address 10.0.254.254
    netmask 255.255.255.255

auto enp5s0
allow-hotplug enp5s0
iface enp5s0 inet manual
```

frr configured to peer using interfaces rather than ips:

```
router bgp 64513
  neighbor enp5s0 interface remote-as internal
  ...
```

routing enabled in the kernel:

```
net.ipv4.conf.all.forwarding=1
net.ipv6.conf.all.forwarding=1
```

Ansible is used to demonstrate the ease of templating such a configuration.  We can easily loop through a list of interfaces and enable bgp:

```
router bgp {{ router_as }}
  bgp router-id {{ router_ip }}
{% for i in ansible_interfaces|sort %}
{% if i.startswith('enp') or i.startswith('eno') or i.startswith('eth') %}
  neighbor {{i}} interface remote-as internal
{% endif %}
{% endfor %}
  address-family ipv4 unicast
    network {{ router_net }}
```

Resulting in a device where I can simply plug-and-play on any port:

```
 router bgp 64513
   bgp router-id 10.0.254.254
+  neighbor enp2s4f0 interface remote-as internal
+  neighbor enp2s4f1 interface remote-as internal
+  neighbor enp3s6f0 interface remote-as internal
+  neighbor enp3s6f1 interface remote-as internal
+  neighbor enp3s8f0 interface remote-as internal
+  neighbor enp3s8f1 interface remote-as internal
+  neighbor enp4s0 interface remote-as internal
   neighbor enp5s0 interface remote-as internal
   address-family ipv4 unicast
     network 10.0.254.254/32
```

## L2 overlays with BGP EVPN

BGP is capable of dynamically propogating vni endponts for establishing unicast vxlans.

In this unnumbered environment, we simply activate the protocol on each interface neighbor:

```
  address-family l2vpn evpn
    neighbor eno1 activate
    neighbor eno2 activate
    advertise-all-vni
    advertise ipv4 unicast
```

The same is done on the spines.

Then bring up a vxlan interface and bridge using our existing loopback address for the local tunnel ip:

```
ip link add vxlan100 type vxlan id 100 dstport 4789 local 10.0.200.3 nolearning
brctl addbr br100
brctl addif br100 vxlan100
ip link set up dev br100
ip link set up dev vxlan10
```

Once this is set up on some hypervisors we want to participate in the vxlan, they should be able to see prefixes to the other vxlan endpoints:

```
root@apollolake-6e10:~# vtysh <<< "show bgp evpn route"

Hello, this is FRRouting (version 7.5.1).
Copyright 1996-2005 Kunihiro Ishiguro, et al.

apollolake-6e10# show bgp evpn route
BGP table version is 1, local router ID is 10.0.200.2
Status codes: s suppressed, d damped, h history, * valid, > best, i - internal
Origin codes: i - IGP, e - EGP, ? - incomplete
EVPN type-1 prefix: [1]:[ESI]:[EthTag]:[IPlen]:[VTEP-IP]
EVPN type-2 prefix: [2]:[EthTag]:[MAClen]:[MAC]:[IPlen]:[IP]
EVPN type-3 prefix: [3]:[EthTag]:[IPlen]:[OrigIP]
EVPN type-4 prefix: [4]:[ESI]:[IPlen]:[OrigIP]
EVPN type-5 prefix: [5]:[EthTag]:[IPlen]:[IP]

   Network          Next Hop            Metric LocPrf Weight Path
                    Extended Community
Route Distinguisher: 10.0.200.2:2
*> [3]:[0]:[32]:[10.0.200.2]
                    10.0.200.2(apollolake-6e10)
                                                       32768 i
                    ET:8 RT:64791:100
Route Distinguisher: 10.0.200.2:3
*> [3]:[0]:[32]:[10.0.200.2]
                    10.0.200.2(apollolake-6e10)
                                                       32768 i
                    ET:8 RT:64791:200
Route Distinguisher: 10.0.200.3:2
*> [3]:[0]:[32]:[10.0.200.3]
                    10.0.200.3(apollolake-179e)
                                                           0 65451 i
                    RT:65451:100 ET:8
Route Distinguisher: 10.0.200.3:3
*> [3]:[0]:[32]:[10.0.200.3]
                    10.0.200.3(apollolake-179e)
                                                           0 65451 i
                    RT:65451:200 ET:8

Displayed 4 prefixes (4 paths)
```

And if we ping broadcast from one node, we should be able to tcpdump it from other:

```
root@apollolake-179e:~# ping -b 255.255.255.255 -I br100
WARNING: pinging broadcast address
```

...

```
root@apollolake-6e10:~# tcpdump -i br100
tcpdump: verbose output suppressed, use -v[v]... for full protocol decode
listening on br100, link-type EN10MB (Ethernet), snapshot length 262144 bytes
02:19:41.766268 IP apollolake-179e > 255.255.255.255: ICMP echo request, id 29320, seq 1, length 64
02:19:42.798323 IP apollolake-179e > 255.255.255.255: ICMP echo request, id 29320, seq 2, length 64
02:19:49.565221 IP apollolake-179e > 255.255.255.255: ICMP echo request, id 30512, seq 1, length 64
```

And now we have a purely programmable layer 2 segment running over our ideal L3 substrate!

## Booting VMs on the vxlan

Now the we have a distributed L2 segment, lets connect some VMs, get them addressed, talking to eachother, and talking to the internet.

We'll use LXD.

I've given each hypervisor's br100 an address in the 172.16.0.0/24 space.  172.16.0.1 looks like this:

```
4: vxlan100: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue master br100 state UNKNOWN group default qlen 1000
    link/ether 72:94:76:9c:3d:2a brd ff:ff:ff:ff:ff:ff
    inet6 fe80::7094:76ff:fe9c:3d2a/64 scope link 
       valid_lft forever preferred_lft forever
5: br100: <BROADCAST,MULTICAST,UP,LOWER_UP> mtu 1500 qdisc noqueue state UP group default qlen 1000
    link/ether 3e:17:ca:87:9c:02 brd ff:ff:ff:ff:ff:ff
    inet 172.16.0.1/24 scope global br100
       valid_lft forever preferred_lft forever
    inet6 fe80::3c17:caff:fe87:9c02/64 scope link 
       valid_lft forever preferred_lft forever
```

Thanks to the `advertise all connected` frr configuration, the bgp deployment learns that the hypervisors all have paths into 172.16.0.0/24:

```
vtysh <<< "show ip bgp"
...
*  172.16.0.0/24    enp3s6f0                 0             0 65451 ?
*>                  enp3s8f0                 0             0 65489 ?
```

Now we tell lxd to use this bridge for our default 'profile':

```
lxc profile edit default
...
    parent: br100
...
```

I've created a quick dhcp server on the segment with a static ip:

```
lxc launch images:debian/12 vxlan100-dhcp
...
apt isntall isc-dchp-server
...
root@vxlan100-dhcp:~# cat /etc/systemd/network/eth0.network 
[Match]
Name=eth0
[Network]
Address=172.16.0.10/24
Gateway=172.16.0.1
DNS=1.1.1.1
root@vxlan100-dhcp:~# cat /etc/dhcp/dhcpd.conf
...
subnet 172.16.0.0 netmask 255.255.255.0 {
  range 172.16.0.11 172.16.0.254;
  option domain-name-servers 1.1.1.1;
  option routers 172.16.0.1;
  default-lease-time 600;
  max-lease-time 7200;
}
```

Notice we're using one of the hypervisor's /24 bridge addresses for the default gateway.  We'll discuss the issue with this later.

With dhcp in place, we can launch arbitrary instances:

```
lxc launch images:debian/12 iperf1
lxc launch images:debian/12 iperf2
```

Note they end up on random nodes, but they do indeed get addresses from vxlan100-dhcp:

```
~# lxc ls
+---------------+---------+--------------------+------+-----------------+-----------+-----------------+
|     NAME      |  STATE  |        IPV4        | IPV6 |      TYPE       | SNAPSHOTS |    LOCATION     |
+---------------+---------+--------------------+------+-----------------+-----------+-----------------+
| iperf1        | RUNNING | 172.16.0.15 (eth0) |      | CONTAINER       | 0         | apollolake-179e |
+---------------+---------+--------------------+------+-----------------+-----------+-----------------+
| iperf2        | RUNNING | 172.16.0.14 (eth0) |      | CONTAINER       | 0         | apollolake-6e10 |
+---------------+---------+--------------------+------+-----------------+-----------+-----------------+
| vxlan100-dhcp | RUNNING | 172.16.0.10 (eth0) |      | CONTAINER       | 0         | apollolake-179e |
+---------------+---------+--------------------+------+-----------------+-----------+-----------------+
```

They can access the internet:

```
root@iperf1:~# apt install iperf
...
Unpacking iperf (2.1.7+dfsg1-1) ...
Setting up iperf (2.1.7+dfsg1-1) ...
```

And for the grand reveal, lets check the inter-node bandwidth of the overlay:

```
$ lxc exec iperf1 -- iperf -s
...
$ lxc exec iperf2 -- iperf -c 172.16.0.15
------------------------------------------------------------
Client connecting to 172.16.0.15, TCP port 5001
TCP window size: 16.0 KByte (default)
------------------------------------------------------------
[  1] local 172.16.0.14 port 52832 connected with 172.16.0.15 port 5001 (icwnd/mss/irtt=14/1448/510)
[ ID] Interval       Transfer     Bandwidth
[  1] 0.0000-10.0261 sec  1.06 GBytes   908 Mbits/sec
```

908 Mbits/sec from VM to VM, between physical nodes, over the vxlan.

## North/South vxlan traffic

North / South traffic in and out of the vxlan is something I havent yet found a good solution for.  Since we're using only one of the hypervisors' /24 bridge addresses for the default gateway of the virtual network, if this node is rebooted, the entire segment will lose internet access.

I tried using keepalived on the bridges, but this didn't initially seem to work well with bgp.  I have yet to troubleshoot why.  I may or may not continue to investigate this.

Even if keepalived did work, we have an issue with less-than-ideal routes in and out of the segment.  If gateway 172.16.0.1 happens to exist on hypervisor-0 at a given time, and a vm on hypervisor-1 wants to talk to the internet, the traffic will have to make an extra hop from hypervisor-1, through hypervisor-0, and then to the upstream router.  Perhaps some of this can be alleviated by placing the vxlan gateways on the spines instead of the hypervisors, since all the traffic flows through them anyway.  Even then, we will be limited by the capacity of a single spine for north/south traffic.  It stands to reason that we can't expect to solve all L2 problems, hence the motivation to implement a pure L3 network in the first place.

Perhaps instead of hacking L2 technologies to build some sort of pseudo-highly-available north/south vxlan gateways, we take a software-operator approach.  Consider the following:

- a python or golang daemon on each hypervisor that watches LXD and brings up an ip in the vxlan address space whenever "i become the database-leader".
- this will take advantage of LXD's dqlite CP properties; we will not enter a split brain.
- this will take advantage of our BGP configuration; when a node assumes an address, it will start advertising that network.
- when a node that previously had the gateway address finds it is no longer the raft master, it will drop the address.
- tie directly into dqlite on disk, skipping the LXD api.  this will hopefully improve stability and compute efficiency.
- if dealing with many L2 segments/gateways, hash-map gateways accross online cluster members rather than using the "database-leader".
- going a step farther, incorporate addresses and physical locations of known instances while choosing a target cluster node for a slight "free" improvement in locality

This approach would afford us a kubernetes-like "eventual availability" quality for our vxlan default gateways, while likely being a bit more robust and fault tolerant than some sort of glued-together assortment of keepalived and virtual routers.

The only thing left would be to implement redundant dhcpd servers, which is an easy task.

## Performance

Test router is a Supermicro Atom D525 system with integrated 1gb interfaces.  I tested latency and bandwidth through a standard switch, and then the D525 linux router, using similarly specced systems as the client and server.

pure l2:

```
client---hp_procurve---server
```

```
943 Mbits/sec
```

```
10 packets transmitted, 10 received, 0% packet loss, time 9219ms
rtt min/avg/max/mdev = 0.195/0.240/0.383/0.050 ms
```

pure l3:

```
client---D525_router---server
```

```
941 Mbits/sec
```

```
10 packets transmitted, 10 received, 0% packet loss, time 9112ms
rtt min/avg/max/mdev = 0.429/0.520/0.578/0.055 ms
```

While routing iperf traffic, the D525 reaches a maximum utilization of roughly 10% of one cpu core.

The x86 router appears to offer similar bandwidth to the switch, and about double the latency.  Packets per second will suffer compared to a router with purpose-built hardware.

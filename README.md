# bgp-unnumbered

A reference implementation of bgp routing over unnumbered network interfaces.

This is achieved by advertising ipv4 prefixes over ipv6 link-local nexthops.

By trading a little more day-1 complexity in setup, we gain significant day-2 operation benefits.  This architecture allows switch-like ease of configuration of individual links, while maintaining a non-blocking fault-tolerant network architecture that is ( ideally ) vendor-agnostic.

Allowing hypervisors to take part in the layer-3 topology allows an operator to achieve higher overall utilization of each individual link, more freely manipulate virtual layer-2 segments across dispersed physical infrastructre ( vxlans ), and better investigate issues when they do arise with the more robust layer-3 toolset.

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

## Testing

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

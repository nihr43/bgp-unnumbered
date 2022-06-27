# bgp-unnumbered

A reference implementation of bgp routing over unnumbered network interfaces.

This is achieved by advertising ipv4 prefixes over ipv6 link-local nexthops.

This allows layer-2 like ease of configuration of individual links, while maintaining a non-blocking fault-tolerant network architecture that is ( ideally ) vendor-agnostic.

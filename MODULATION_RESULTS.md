# X310 payload limits by hop rate — 250 kbit/s

## BPSK — 8 ms guard, 20 ms TX lead

| Hops per second | Calculated max payload (bytes) | Total on-air packet size (bytes) |
|---:|---:|---:|
| 8 | 1024 | 1064 |
| 9 | 1024 | 1064 |
| 10 | 1024 | 1064 |
| 11 | 987 | 1027 |
| 12 | 750 | 790 |
| 13 | 550 | 590 |
| 14 | 378 | 418 |
| 15 | 229 | 269 |
| 16 | 99 | 139 |

## 2FSK — 1 ms guard, 8 ms TX lead

| Hops per second | Calculated max payload (bytes) | Total on-air packet size (bytes) |
|---:|---:|---:|
| 8 | 1024 | 1064 |
| 9 | 1024 | 1064 |
| 10 | 1024 | 1064 |
| 11 | 1024 | 1064 |
| 12 | 1024 | 1064 |
| 13 | 1024 | 1064 |
| 14 | 1024 | 1064 |
| 15 | 1024 | 1064 |
| 16 | 912 | 952 |
| 17 | 797 | 837 |
| 18 | 695 | 735 |
| 19 | 603 | 643 |
| 20 | 521 | 561 |
| 21 | 447 | 487 |
| 22 | 379 | 419 |
| 23 | 317 | 357 |
| 24 | 261 | 301 |
| 25 | 209 | 249 |
| 26 | 160 | 200 |
| 27 | 116 | 156 |
| 28 | 75 | 115 |
| 29 | 36 | 76 |

## MSK — 1 ms guard, 8 ms TX lead

| Hops per second | Calculated max payload (bytes) | Total on-air packet size (bytes) |
|---:|---:|---:|
| 8 | 1024 | 1064 |
| 9 | 1024 | 1064 |
| 10 | 1024 | 1064 |
| 11 | 1024 | 1064 |
| 12 | 1024 | 1064 |
| 13 | 1024 | 1064 |
| 14 | 1024 | 1064 |
| 15 | 1024 | 1064 |
| 16 | 912 | 952 |
| 17 | 797 | 837 |
| 18 | 695 | 735 |
| 19 | 603 | 643 |
| 20 | 521 | 561 |
| 21 | 447 | 487 |
| 22 | 379 | 419 |
| 23 | 317 | 357 |
| 24 | 261 | 301 |
| 25 | 209 | 249 |
| 26 | 160 | 200 |
| 27 | 116 | 156 |
| 28 | 75 | 115 |
| 29 | 36 | 76 |

## GMSK — 1 ms guard, 8 ms TX lead

| Hops per second | Calculated max payload (bytes) | Total on-air packet size (bytes) |
|---:|---:|---:|
| 8 | 1024 | 1064 |
| 9 | 1024 | 1064 |
| 10 | 1024 | 1064 |
| 11 | 1024 | 1064 |
| 12 | 1024 | 1064 |
| 13 | 1024 | 1064 |
| 14 | 1024 | 1064 |
| 15 | 1024 | 1064 |
| 16 | 911 | 951 |
| 17 | 796 | 836 |
| 18 | 694 | 734 |
| 19 | 603 | 643 |
| 20 | 521 | 561 |
| 21 | 446 | 486 |
| 22 | 378 | 418 |
| 23 | 317 | 357 |
| 24 | 260 | 300 |
| 25 | 208 | 248 |
| 26 | 160 | 200 |
| 27 | 115 | 155 |
| 28 | 74 | 114 |
| 29 | 36 | 76 |

## QPSK — 1 ms guard, 8 ms TX lead

| Hops per second | Calculated max payload (bytes) | Total on-air packet size (bytes) |
|---:|---:|---:|
| 8 | 1024 | 1064 |
| 9 | 1024 | 1064 |
| 10 | 1024 | 1064 |
| 11 | 1024 | 1064 |
| 12 | 1024 | 1064 |
| 13 | 1024 | 1064 |
| 14 | 1024 | 1064 |
| 15 | 1024 | 1064 |
| 16 | 912 | 952 |
| 17 | 797 | 837 |
| 18 | 695 | 735 |
| 19 | 603 | 643 |
| 20 | 521 | 561 |
| 21 | 447 | 487 |
| 22 | 379 | 419 |
| 23 | 317 | 357 |
| 24 | 261 | 301 |
| 25 | 209 | 249 |
| 26 | 160 | 200 |

# B205 peer-to-peer — measured hopping results

Signed generic-v5; 915/916 MHz; 2 Msps. BPSK, 2FSK, MSK and GMSK: 250 kbit/s.
QPSK: 500 kbit/s (250 kbit/s did not pass). Experimental rows use 5 ms guards
and 10 ms TX leads; the 1 hop/s baseline uses 100 ms guards and 50 ms SYNC lead.
Payloads are tested sizes, not calculated maxima. Total on-air DATA size includes
signing, error correction and framing; ACK/SYNC packets are separate.
The highest qualified rate for each modulation below passed both directions, simultaneous traffic and
restart recovery for 20 seconds per mode at 2.5 packets/s per sender.
These are measured workload limits, not absolute hardware limits; normal GUI
limits remain unchanged. Setup and method: [UHD setup](docs/UHD_SETUP.md#b205-timing-experiments).

## BPSK

| Hops per second | Verified payload (bytes) | Total on-air packet size (bytes) |
|---:|---:|---:|
| 6 | 32 | 296 |

## 2FSK

| Hops per second | Verified payload (bytes) | Total on-air packet size (bytes) |
|---:|---:|---:|
| 1 | 1024 | 2280 |
| 17 | 32 | 296 |

The 1 hop/s baseline passed 10 seconds per traffic mode at 2 packets/s without a restart test.

## MSK

| Hops per second | Verified payload (bytes) | Total on-air packet size (bytes) |
|---:|---:|---:|
| 9 | 32 | 296 |

## GMSK

| Hops per second | Verified payload (bytes) | Total on-air packet size (bytes) |
|---:|---:|---:|
| 17 | 32 | 296 |

## QPSK — 500 kbit/s

| Hops per second | Verified payload (bytes) | Total on-air packet size (bytes) |
|---:|---:|---:|
| 6 | 32 | 296 |

# B205 frequency-span checks

Tested frequency pairs, not exact hardware limits or qualification of every frequency between the endpoints.
Bit rates: 250 kbit/s, except QPSK at 500 kbit/s. Payload sizes are verified test sizes, not calculated maxima.

## Faster hopping — 32-byte payloads, 296-byte on-air packets

| Modulation | Hops/s | Lower frequency (MHz) | Upper frequency (MHz) | Span (MHz) |
|---|---:|---:|---:|---:|
| BPSK | 6 | 915 | 916 | 1 |
| 2FSK | 17 | 865.5 | 965.5 | 100 |
| MSK | 9 | 865.5 | 965.5 | 100 |
| GMSK | 17 | 910.5 | 920.5 | 10 |
| QPSK | 6 | 865.5 | 965.5 | 100 |

## Wider hopping — 1 hop/s, 1 packet/s per sender

| Modulation | Lower frequency (MHz) | Upper frequency (MHz) | Span (MHz) | Payload (bytes) | Total on-air packet (bytes) |
|---|---:|---:|---:|---:|---:|
| BPSK | 70.5 | 5999.5 | 5929 | 128 | 488 |
| 2FSK | 415.5 | 1415.5 | 1000 | 1024 | 2280 |
| MSK | 415.5 | 1415.5 | 1000 | 1024 | 2280 |
| GMSK | 415.5 | 1415.5 | 1000 | 1024 | 2280 |

QPSK's larger-packet runs were not qualified because dev10 reported receive overflows.
2FSK and GMSK passed their 1,000 MHz hopping pairs, but failed fixed-frequency simultaneous traffic at 1,415.5 MHz.
[Measurement conditions and limitations](docs/UHD_SETUP.md#b205-frequency-span-measurements).

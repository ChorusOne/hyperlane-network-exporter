hyperlane-network-exporter
==========================

Exports latest checkpoint recorded in Ethereum merkle tree hook contract for Hyperlane.


Available metrics
-----------------


`hyperlane_contract_latest_checkpoint` -- latest checkpoint recorded in contract,
                                          normally correlates with [one exported by AVS](https://docs.hyperlane.xyz/docs/operate/validators/monitoring-alerting#metrics)

Dimensions:
 - `network` one of `mainnet`, `holesky`


Installation
------------
This program uses [Pipenv](https://pipenv.pypa.io/en/latest/) to manage
dependencies. It have been tested with Python 3.12

To create dedicated virtual environment and install dependencies, after
cloning an application, navigate to its root folder and invoke

```bash
pipenv sync
```

Running
--------

There is only one parameter, which should be address of Ethereum RPC node.

After creating config file, run application like

```bash
pipenv run python3 hyperlane_network_exporter.py --ethereum-rpc http://mynode:8545
```

The Ethereum network (mainnet or holesky) is determined using chain id value from the RPC node

To change host and port for Prometheus metrics server, use following parameters

```bash
  -H HOST, --host HOST  Listen on this host.
  -P PORT, --port PORT  Listen on this port.
```

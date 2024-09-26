import argparse
import asyncio
import enum
import functools
import json
import logging
import pathlib
import sys
import typing

from aiohttp import client, web
from prometheus_async import aio
from prometheus_client import Gauge
from web3 import AsyncWeb3
from web3.contract import AsyncContract
from web3.eth import AsyncEth
from web3.providers.rpc import AsyncHTTPProvider


logger = logging.getLogger(__name__)


# #############
# Command line
DEFAULT_INTERVAL_MS = 30000

arg_parser = argparse.ArgumentParser("Hyperlane network metrics exporter.")
arg_parser.add_argument(
    "-e",
    "--ethereum-rpc",
    help="Ethereum RPC base URL",
    required=True,
)
arg_parser.add_argument(
    "-i",
    "--interval-ms",
    type=int,
    help="How often to update value from contract",
    default=DEFAULT_INTERVAL_MS,
)
arg_parser.add_argument(
    "-H", "--host", default="127.0.0.1", help="Listen on this host."
)
arg_parser.add_argument(
    "-P", "--port", type=int, default=39339, help="Listen on this port."
)


# ############################
# Supported Ethereum networks
class SupportedNetworks(str, enum.Enum):
    MAINNET = "mainnet"
    HOLESKY = "holesky"

    def __str__(self) -> str:
        return self.value

    def hyperlane_merkle_tree_hook_contract(self) -> str:
        # See https://docs.hyperlane.xyz/docs/reference/contract-addresses#merkle-tree-hook
        match self.value:
            case SupportedNetworks.HOLESKY:
                return "0x98AAE089CaD930C64a76dD2247a2aC5773a4B8cE"
            case SupportedNetworks.MAINNET:
                return "0x48e6c30B97748d1e2e03bf3e9FbE3890ca5f8CCA"
        raise RuntimeError(
            "Can not derive hyperlane merkle tree hook address for network"
        )


# ########
# Metrics
hyperlane_contract_latest_checkpoint = Gauge(
    name="hyperlane_contract_latest_checkpoint",
    documentation="Latest checkpoint acknowledged by Hyperlane contract",
    labelnames=["network"],
)


# ####################
# Aiohttp & web3 apps
async def start_exporter_app(app: web.Application) -> None:
    exporter: HyperlaneContractExporter = app[exporter_app_key]
    await exporter.w3.provider.cache_async_session(exporter.session)  # type: ignore[attr-defined]
    await exporter.init()
    # Acquire data once to verify its working
    await exporter.tick()
    exporter.start()


async def stop_exporter_app(app: web.Application) -> None:
    exporter: HyperlaneContractExporter = app[exporter_app_key]
    await exporter.stop()


def get_application(exporter: "HyperlaneContractExporter") -> web.Application:
    app = web.Application()
    app[exporter_app_key] = exporter
    app.router.add_get("/metrics", aio.web.server_stats)
    app.on_startup.append(start_exporter_app)
    app.on_shutdown.append(stop_exporter_app)
    return app


def get_web3_provider(ethereum_rpc_url: str) -> AsyncWeb3:
    return AsyncWeb3(
        provider=AsyncHTTPProvider(ethereum_rpc_url),
        modules={"eth": (AsyncEth,)},
    )


def get_hyperlane_merkle_tree_hook_contract_abi() -> typing.Any:
    contents = (
        pathlib.Path(__file__).parent / "contract/MerkleTreeHook.json"
    ).read_text()
    return json.loads(contents)


def get_hyperlane_merkle_tree_hook_contract(
    web3: AsyncWeb3, network: SupportedNetworks
) -> AsyncContract:
    abi = get_hyperlane_merkle_tree_hook_contract_abi()
    address = network.hyperlane_merkle_tree_hook_contract()
    contract: AsyncContract = web3.eth.contract(address=address, abi=abi)  # type: ignore[call-overload]
    return contract


class HyperlaneContractExporter:

    def __init__(
        self,
        rpc_address: str,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        *,
        loop: asyncio.AbstractEventLoop,
    ):
        self.rpc_address = rpc_address
        self.interval_ms = interval_ms
        self.loop = loop
        self.session = client.ClientSession(loop=loop)
        self.w3 = get_web3_provider(self.rpc_address)
        self.stopping = False
        self.stopped = asyncio.Event()

    @functools.cached_property
    def contract(self) -> AsyncContract:
        return get_hyperlane_merkle_tree_hook_contract(self.w3, self.network)

    def on_runner_task_done(self, *args: typing.Any) -> None:
        self.stopped.set()

    def start(self) -> None:
        self._runner_task = self.loop.create_task(self.run())
        # Raise event when task is stopped
        self._runner_task.add_done_callback(self.on_runner_task_done)

    async def stop(self) -> None:
        logger.info("Gracefully shutting down application")
        self.stopping = True
        self._runner_task.cancel()
        if not self.stopped.is_set():
            await self.stopped.wait()
        await self.session.close()
        logger.info("Stopped components, will exit")

    async def sleep(self) -> None:
        await asyncio.sleep(self.interval_ms / 1000)

    async def init(self) -> None:
        # Resolve network from RPC
        chain_id = await self.w3.eth.chain_id
        if chain_id == 1:
            logger.info("Discovered Ethereum network as MAINNET")
            self.network = SupportedNetworks.MAINNET
        elif chain_id == 17000:
            logger.info("Discovered Ethereum network as HOLESKY")
            self.network = SupportedNetworks.HOLESKY
        else:
            raise RuntimeError("Unsupported network with chain id %s", chain_id)

    async def tick(self) -> None:
        _, value = await self.contract.functions.latestCheckpoint().call()
        logger.info("Fetched checkpoint index from contract: %s", value)
        hyperlane_contract_latest_checkpoint.labels(self.network).set(value)

    async def run(self) -> None:
        """Infinite loop that spawns checker tasks."""
        while not self.stopping:
            self.loop.create_task(self.tick())
            await self.sleep()
        self.stopped.set()


exporter_app_key: web.AppKey[HyperlaneContractExporter] = web.AppKey(
    "exporter", HyperlaneContractExporter
)


def main() -> None:
    args = arg_parser.parse_args()
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    exporter = HyperlaneContractExporter(args.ethereum_rpc, args.interval_ms, loop=loop)
    app = get_application(exporter)
    web.run_app(
        app, host=args.host, port=args.port, loop=loop, handler_cancellation=True
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s.%(msecs)03d %(levelname)s %(module)s - %(funcName)s: %(message)s",
    )
    main()

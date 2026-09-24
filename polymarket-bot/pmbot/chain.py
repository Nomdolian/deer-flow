"""On-chain work: allowances, redemption, and set split/merge.

Three things the CLOB API cannot do for you, because they are Polygon
transactions rather than signed orders:

  * **Allowances** — until USDC and the CTF ERC-1155 are approved to the
    exchange contracts, every order you sign is rejected. One-time, a few
    cents. (Magic/email wallets set these automatically; EOAs do not.)
  * **Redemption** — winning outcome tokens redeem 1:1 for USDC through the
    CTF contract after UMA resolves. Nothing does it for you, and unswept
    positions are dead capital.
  * **Split / merge** — 1 USDC <-> one complete set of outcome tokens. Merging
    a set you already hold returns the dollar immediately instead of waiting
    for resolution.

web3 is an optional dependency: paper mode, the tests and the backtester
never import it. Addresses come from the SDK's own contract config so they
cannot drift from what the signer uses, and every address is printed before
anything is sent.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

MAX_UINT256 = 2**256 - 1
USDC_DECIMALS = 6
# A binary condition's two outcome slots. Neg-risk questions are also binary
# at the condition level: the "which of N wins" structure is N conditions.
BINARY_INDEX_SETS = [1, 2]
BYTES32_ZERO = "0x" + "00" * 32

ERC20_ABI = [
    {"name": "approve", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "allowance", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "decimals", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "uint8"}]},
    {"name": "symbol", "type": "function", "stateMutability": "view",
     "inputs": [], "outputs": [{"name": "", "type": "string"}]},
]

CTF_ABI = [
    {"name": "setApprovalForAll", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "operator", "type": "address"}, {"name": "approved", "type": "bool"}],
     "outputs": []},
    {"name": "isApprovedForAll", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}, {"name": "operator", "type": "address"}],
     "outputs": [{"name": "", "type": "bool"}]},
    {"name": "balanceOf", "type": "function", "stateMutability": "view",
     "inputs": [{"name": "account", "type": "address"}, {"name": "id", "type": "uint256"}],
     "outputs": [{"name": "", "type": "uint256"}]},
    {"name": "redeemPositions", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "collateralToken", "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "indexSets", "type": "uint256[]"}],
     "outputs": []},
    {"name": "splitPosition", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "collateralToken", "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "partition", "type": "uint256[]"},
                {"name": "amount", "type": "uint256"}],
     "outputs": []},
    {"name": "mergePositions", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "collateralToken", "type": "address"},
                {"name": "parentCollectionId", "type": "bytes32"},
                {"name": "conditionId", "type": "bytes32"},
                {"name": "partition", "type": "uint256[]"},
                {"name": "amount", "type": "uint256"}],
     "outputs": []},
]

# The neg-risk adapter wraps the CTF for multi-outcome events: positions in a
# neg-risk market must be redeemed through it, not through the CTF directly.
NEG_RISK_ADAPTER_ABI = [
    {"name": "redeemPositions", "type": "function", "stateMutability": "nonpayable",
     "inputs": [{"name": "conditionId", "type": "bytes32"},
                {"name": "amounts", "type": "uint256[]"}],
     "outputs": []},
]


@dataclass
class Contracts:
    """Addresses of everything we touch. Sourced from the SDK by default so
    they match what the order signer uses; overridable from bot.yaml for a
    deployment that is ahead of the pinned SDK.
    """

    collateral: str
    conditional_tokens: str
    exchange: str
    neg_risk_exchange: str
    neg_risk_adapter: str
    exchange_v2: str = ""
    neg_risk_exchange_v2: str = ""

    def spenders(self) -> list[tuple[str, str]]:
        """(label, address) pairs that need an allowance.

        Both V1 and V2 exchanges are approved: V2 is what signs today, and V1
        still settles anything left over from before the cutover. An approval
        to a contract you never trade against costs one transaction and
        nothing else.
        """
        candidates = [
            ("exchange_v2", self.exchange_v2),
            ("neg_risk_exchange_v2", self.neg_risk_exchange_v2),
            ("exchange", self.exchange),
            ("neg_risk_exchange", self.neg_risk_exchange),
            ("neg_risk_adapter", self.neg_risk_adapter),
        ]
        return [(label, address) for label, address in candidates if address]


def load_contracts(chain_id: int = 137, overrides: dict[str, str] | None = None) -> Contracts:
    """Read the SDK's contract config, then apply any bot.yaml overrides."""
    values: dict[str, str] = {}
    try:
        from py_clob_client_v2.config import get_contract_config

        config = get_contract_config(chain_id)
        values = {
            "collateral": config.collateral,
            "conditional_tokens": config.conditional_tokens,
            "exchange": config.exchange,
            "neg_risk_exchange": config.neg_risk_exchange,
            "neg_risk_adapter": config.neg_risk_adapter,
            "exchange_v2": getattr(config, "exchange_v2", "") or "",
            "neg_risk_exchange_v2": getattr(config, "neg_risk_exchange_v2", "") or "",
        }
    except ImportError:
        log.warning("py-clob-client-v2 not installed; contract addresses must come from bot.yaml")
    values.update({k: v for k, v in (overrides or {}).items() if v})
    missing = [k for k in ("collateral", "conditional_tokens") if not values.get(k)]
    if missing:
        raise RuntimeError(f"no address configured for: {', '.join(missing)}")
    return Contracts(**values)  # type: ignore[arg-type]


def to_units(amount: float, decimals: int = USDC_DECIMALS) -> int:
    """USDC and CTF positions are both 6-decimal fixed point on Polygon."""
    return round(amount * 10**decimals)


def from_units(amount: int, decimals: int = USDC_DECIMALS) -> float:
    return amount / 10**decimals


class ChainClient:
    """Signed Polygon transactions for the handful of things the API cannot do.

    Every mutating call takes dry_run, and dry_run is the default everywhere
    it is wired in: nothing sends a transaction until a human has read what it
    would send.
    """

    def __init__(self, rpc_url: str, private_key: str, contracts: Contracts,
                 chain_id: int = 137, w3: Any = None, gas_price_multiplier: float = 1.2):
        self.contracts = contracts
        self.chain_id = chain_id
        self.gas_price_multiplier = gas_price_multiplier
        self._w3 = w3
        self._rpc_url = rpc_url
        self._private_key = private_key
        self._account = None

    # ---- lazy web3 --------------------------------------------------------

    @property
    def w3(self):
        if self._w3 is None:
            try:
                from web3 import Web3
                from web3.middleware import ExtraDataToPOAMiddleware
            except ImportError as exc:  # pragma: no cover - optional dependency
                raise RuntimeError(
                    "on-chain features need web3: pip install 'pmbot[chain]'"
                ) from exc
            w3 = Web3(Web3.HTTPProvider(self._rpc_url))
            # Polygon is proof-of-authority: without this, block parsing fails.
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            self._w3 = w3
        return self._w3

    @property
    def account(self):
        if self._account is None:
            if not self._private_key:
                raise RuntimeError("PRIVATE_KEY is not set; cannot sign transactions")
            self._account = self.w3.eth.account.from_key(self._private_key)
        return self._account

    @property
    def address(self) -> str:
        return self.account.address

    def _contract(self, address: str, abi: list):
        return self.w3.eth.contract(address=self.w3.to_checksum_address(address), abi=abi)

    def _send(self, function, dry_run: bool = True, gas_limit: int | None = None) -> str | None:
        """Build, sign, send, and wait for one transaction."""
        if dry_run:
            log.info("[dry-run] would send %s", function.fn_name)
            return None
        tx = function.build_transaction({
            "from": self.address,
            "nonce": self.w3.eth.get_transaction_count(self.address),
            "chainId": self.chain_id,
        })
        if gas_limit is not None:
            tx["gas"] = gas_limit
        signed = self.w3.eth.account.sign_transaction(tx, self._private_key)
        tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=180)
        if receipt.get("status") != 1:
            raise RuntimeError(f"transaction reverted: {tx_hash.hex()}")
        log.info("%s confirmed in %s", function.fn_name, tx_hash.hex())
        return tx_hash.hex()

    # ---- balances ---------------------------------------------------------

    def usdc_balance(self) -> float:
        token = self._contract(self.contracts.collateral, ERC20_ABI)
        return from_units(token.functions.balanceOf(self.address).call())

    def gas_balance(self) -> float:
        return float(self.w3.from_wei(self.w3.eth.get_balance(self.address), "ether"))

    def token_balance(self, token_id: str) -> float:
        ctf = self._contract(self.contracts.conditional_tokens, CTF_ABI)
        return from_units(ctf.functions.balanceOf(self.address, int(token_id)).call())

    def collateral_info(self) -> tuple[str, int]:
        """symbol and decimals of the configured collateral.

        Called before any approval so the operator can see they are about to
        approve USDC and not something else.
        """
        token = self._contract(self.contracts.collateral, ERC20_ABI)
        return token.functions.symbol().call(), token.functions.decimals().call()

    # ---- allowances -------------------------------------------------------

    def allowance_status(self) -> list[dict]:
        """What is approved right now, per spender. Read-only."""
        token = self._contract(self.contracts.collateral, ERC20_ABI)
        ctf = self._contract(self.contracts.conditional_tokens, CTF_ABI)
        rows: list[dict] = []
        for label, address in self.contracts.spenders():
            spender = self.w3.to_checksum_address(address)
            rows.append({
                "spender": label,
                "address": spender,
                "usdc_allowance": from_units(
                    token.functions.allowance(self.address, spender).call()
                ),
                "ctf_approved": ctf.functions.isApprovedForAll(self.address, spender).call(),
            })
        return rows

    def ensure_allowances(self, dry_run: bool = True, min_usdc: float = 1_000_000.0) -> list[str]:
        """Approve USDC and the CTF ERC-1155 to every exchange contract.

        Returns the transaction hashes sent (empty on a dry run). Already
        approved spenders are skipped, so this is safe to re-run.
        """
        token = self._contract(self.contracts.collateral, ERC20_ABI)
        ctf = self._contract(self.contracts.conditional_tokens, CTF_ABI)
        sent: list[str] = []
        for row in self.allowance_status():
            spender = row["address"]
            if row["usdc_allowance"] < min_usdc:
                log.info("approving USDC to %s (%s)", row["spender"], spender)
                tx = self._send(token.functions.approve(spender, MAX_UINT256), dry_run)
                if tx:
                    sent.append(tx)
            if not row["ctf_approved"]:
                log.info("approving CTF tokens to %s (%s)", row["spender"], spender)
                tx = self._send(ctf.functions.setApprovalForAll(spender, True), dry_run)
                if tx:
                    sent.append(tx)
        if not sent and not dry_run:
            log.info("allowances already set; nothing to do")
        return sent

    # ---- settlement -------------------------------------------------------

    def redeem(self, condition_id: str, neg_risk: bool = False, amounts: list[int] | None = None,
               dry_run: bool = True) -> str | None:
        """Redeem a resolved position for USDC.

        Neg-risk markets must go through the adapter; everything else goes
        straight to the CTF. Redeeming an unresolved condition reverts, which
        is why the caller checks resolution state first.
        """
        if neg_risk:
            adapter = self._contract(self.contracts.neg_risk_adapter, NEG_RISK_ADAPTER_ABI)
            # The adapter takes explicit per-outcome amounts rather than index sets.
            values = amounts if amounts is not None else [0, 0]
            return self._send(adapter.functions.redeemPositions(condition_id, values), dry_run)
        ctf = self._contract(self.contracts.conditional_tokens, CTF_ABI)
        return self._send(
            ctf.functions.redeemPositions(
                self.w3.to_checksum_address(self.contracts.collateral),
                BYTES32_ZERO,
                condition_id,
                BINARY_INDEX_SETS,
            ),
            dry_run,
        )

    def merge(self, condition_id: str, shares: float, dry_run: bool = True) -> str | None:
        """Burn one complete set per share and take back the USDC.

        Worth doing whenever an unwound arbitrage leaves you holding both
        sides: the dollar comes back now instead of at resolution.
        """
        ctf = self._contract(self.contracts.conditional_tokens, CTF_ABI)
        return self._send(
            ctf.functions.mergePositions(
                self.w3.to_checksum_address(self.contracts.collateral),
                BYTES32_ZERO, condition_id, BINARY_INDEX_SETS, to_units(shares),
            ),
            dry_run,
        )

    def split(self, condition_id: str, shares: float, dry_run: bool = True) -> str | None:
        """Mint a complete set from USDC — the other half of merge."""
        ctf = self._contract(self.contracts.conditional_tokens, CTF_ABI)
        return self._send(
            ctf.functions.splitPosition(
                self.w3.to_checksum_address(self.contracts.collateral),
                BYTES32_ZERO, condition_id, BINARY_INDEX_SETS, to_units(shares),
            ),
            dry_run,
        )

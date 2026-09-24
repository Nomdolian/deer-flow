"""One-time on-chain setup: approve USDC and the CTF ERC-1155 to the exchanges.

Until these approvals exist, every order you sign is rejected. Magic/email
wallets get them automatically; a raw EOA does not.

    python -m scripts.setup_allowances              # dry run: shows the plan
    python -m scripts.setup_allowances --send       # actually approves

Read the dry run before you use --send. It prints every address it will
approve, and the symbol and decimals it read from the collateral contract —
if that does not say USDC with 6 decimals, stop and fix
`chain.contracts.collateral` in bot.yaml rather than approving it.
"""

from __future__ import annotations

import argparse
import logging
import sys

from pmbot.chain import ChainClient, load_contracts
from pmbot.config import Secrets, load_config
from pmbot.logging_utils import setup_logging

EXPECTED_COLLATERAL_SYMBOLS = {"USDC", "USDC.E", "USDCE"}


def main(send: bool, force: bool) -> int:
    setup_logging()
    logging.getLogger("web3").setLevel(logging.WARNING)
    cfg = load_config()
    secrets = Secrets()
    if not secrets.private_key:
        print("PRIVATE_KEY is not set in .env — nothing to sign with.")
        return 1

    contracts = load_contracts(cfg.chain_id, cfg.chain.contracts)
    chain = ChainClient(cfg.chain.rpc_url, secrets.private_key, contracts, cfg.chain_id)

    print(f"wallet          {chain.address}")
    print(f"rpc             {cfg.chain.rpc_url}  (chain {cfg.chain_id})")
    try:
        symbol, decimals = chain.collateral_info()
        gas = chain.gas_balance()
        usdc = chain.usdc_balance()
    except Exception as exc:  # noqa: BLE001 - RPC failures should read plainly
        print(f"\ncould not reach the chain: {exc}")
        return 1

    print(f"collateral      {contracts.collateral}  ({symbol}, {decimals} decimals)")
    print(f"balances        {usdc:.2f} {symbol} · {gas:.4f} POL")
    print(f"CTF             {contracts.conditional_tokens}")
    print("\nspenders to approve:")
    rows = chain.allowance_status()
    for row in rows:
        needs = []
        if row["usdc_allowance"] < 1_000_000:
            needs.append("USDC")
        if not row["ctf_approved"]:
            needs.append("CTF")
        state = ", ".join(needs) + " needed" if needs else "already approved"
        print(f"  {row['spender']:<22} {row['address']}  [{state}]")

    if symbol.upper() not in EXPECTED_COLLATERAL_SYMBOLS and not force:
        print(f"\nREFUSING: collateral reports symbol {symbol!r}, not USDC. Either the "
              f"configured address is wrong or this is not the chain you think it is.\n"
              f"Fix chain.contracts.collateral in bot.yaml, or pass --force if you are "
              f"certain.")
        return 1
    if decimals != 6 and not force:
        print(f"\nREFUSING: collateral reports {decimals} decimals, expected 6.")
        return 1

    pending = [r for r in rows if r["usdc_allowance"] < 1_000_000 or not r["ctf_approved"]]
    if not pending:
        print("\nnothing to do — all allowances are already set.")
        return 0
    if gas <= 0.05:
        print(f"\nnot enough POL for gas ({gas:.4f}); top up the wallet first.")
        return 1

    if not send:
        print(f"\ndry run: {len(pending)} spender(s) need approval. "
              f"Re-run with --send to submit the transactions.")
        return 0

    print(f"\nsending approvals for {len(pending)} spender(s)…")
    sent = chain.ensure_allowances(dry_run=False)
    print(f"done — {len(sent)} transaction(s):")
    for tx in sent:
        print(f"  https://polygonscan.com/tx/{tx}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="submit the approvals (costs gas)")
    parser.add_argument("--force", action="store_true",
                        help="approve even if the collateral contract looks wrong")
    args = parser.parse_args()
    sys.exit(main(args.send, args.force))

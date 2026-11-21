# Viva Preparation: Likely Questions and Answers

**1. Why does this problem need blockchain?**
Several organisations that do not fully trust each other write to one history, and buyers need to verify it without trusting any one of them. A central database lets its owner edit silently; a signed, hash-linked ledger does not.

**2. When would a normal database be enough?**
If one trusted authority controlled all writes and nobody needed independent verification. Blockchain adds value only when trust is shared.

**3. Does the blockchain guarantee the data is true?**
No. It guarantees the data has not changed since it was recorded and shows who signed it. Truth depends on authorised, accountable writers (workshops, insurers, RTO).

**4. What exactly is stored on-chain and what off-chain?**
On-chain: SHA-256 of the VIN, owner addresses, odometer, event type, timestamp, actor, and the SHA-256 of the details. Off-chain: names, descriptions, costs, prices, claim IDs. This keeps personal data off the chain and storage cheap.

**5. How do you detect that off-chain data was edited?**
Re-compute SHA-256 of the canonical JSON and compare with the on-chain `data_hash`. Any change gives a different hash.

**6. What is a Merkle root and why use it?**
A hash that summarises all transactions in a block by repeatedly hashing pairs. Changing any transaction changes the root, so one value commits to the whole block.

**7. Which consensus did you use and why?**
Proof of Authority with round-robin validators. The participants are known organisations, so there is no need for energy-intensive mining. Trade-off: trust rests on the validator set.

**8. How does your chain resist tampering?**
Each block stores the previous block's hash. Editing a block changes its hash and breaks the link in the next block. Signatures and Merkle roots catch edits to transactions.

**9. How is odometer fraud prevented?**
The contract rejects any reading lower than the last recorded value. Also, a fraudulent entry would be signed, so the workshop is identified.

**10. What if a workshop enters a wrong but higher odometer reading?**
The contract cannot know. The record is permanently attributed to that workshop, which creates accountability. Future work: OBD-II attested readings.

**11. What prevents replay or impersonation?**
Ed25519 signatures bind each transaction to the sender's key, the address must match the public key, and a per-account nonce rejects resubmitted transactions.

**12. Why hash the VIN?**
To keep a pseudonymous ID on-chain. Honest limit: VINs are guessable, so it is not secret.

**13. Why custom errors in Solidity?**
Cheaper than revert strings and carry typed arguments (e.g. `OdometerRollback(attempted, lastRecorded)`).

**14. Why `bytes32` for hashes?**
Fixed size, one storage slot, cheaper than `string`.

**15. Public vs permissioned blockchain?**
Public chains give open verification but expose data and cost gas. A vehicle ledger needs identified writers and privacy, so permissioned (e.g. Hyperledger Fabric) fits best in production.

**16. How would this connect to Ethereum?**
A web3.py layer would send the same signed calls to `VehicleLedger.sol`, with MetaMask as the wallet. The rules are already identical.

**17. What are the limitations?**
Local simulated validators, no real identity checks, unsalted VIN hash, no sensor input, Solidity not yet wired to the web app.

**18. How did you test it?**
21 Python tests (valid and invalid transactions, tamper detection, restart persistence, web routes), 8 Hardhat tests, and the seed and tamper scripts.

**19. What happens if a validator is offline?**
In this prototype the scheduled validator must be available. A production PoA network would add timeouts and skip rules, or use BFT consensus such as Fabric's Raft or SmartBFT.

**20. Explain one complete transaction.**
Workshop submits a service record → app builds JSON and hashes it → workshop signs → contract checks role and odometer → block sealed by next validator with Merkle root and signature → details saved off-chain under the hash → report page re-verifies.

## Added for the extended version

**21. How does the lien feature work?**
A Lender-role account places a lien on a VIN; while it is active the contract rejects `transferOwnership` with `LienActive`. Only the placing lender (or the Admin) can release it. This stops a financed vehicle from being sold without the lender's release.

**22. How is the trust score calculated, and can it be trusted?**
It starts at 100 and applies listed deductions (stolen −60, accident −6/−15/−35, lien −8, long service gaps −5, implausible usage −10, many owners, tampered evidence −60). Every deduction is shown with its reason. It is a decision aid based only on ledger records; the weights are design choices, not statistically calibrated.

**23. What does the Tamper lab prove?**
It runs five attacks against an in-memory copy of the chain (edit an odometer, redirect a transfer, delete a block, edit and re-hash a block, edit the off-chain record) and shows that validation detects each. Even a re-hashed block fails because the validator's signature and the next block's link no longer match.

**24. What did the benchmarks show?**
Validation is linear (about 0.3 ms per block, dominated by signature verification). Saving the whole JSON file after each transaction makes throughput fall from about 360 to about 110 tx/s between 50 and 400 blocks (quadratic total cost); an append-only store would remove this.

**25. How does the QR code work?**
A dependency-free QR encoder (byte mode, error-correction level M) generates an SVG linking to the vehicle report. It was verified by decoding the output with OpenCV for 12 different payload sizes.

Protocol Layer
The Protocol Layer defines how components inside Digit World communicate with each other.

Its purpose is to provide:

Deterministic communication
Replay protection
Idempotent operations
Version compatibility
Runtime interoperability
Auditability
The Protocol Layer is considered critical infrastructure for the operation of the world.

Responsibilities
The Protocol Layer is responsible for communication between:

Clients
↕
Central Runtime

Central Runtime
↕
Region Runtime

Region Runtime
↕
Workers

Region Runtime
↕
Trusted Verifiers

Runtime Services
↕
Ledger
The Protocol Layer does not execute gameplay logic.

Its responsibility is transport, validation, serialization, and coordination.

Protocol Domains
Digit World currently defines the following protocol categories:

Client Protocol
Used by:

CLI Client
2D Client
3D Client
Future VR Client
Examples:

Login
Movement
Combat
Contracts
Community Actions
Runtime Protocol
Used by:

Central
Region Runtime
Workers
Examples:

Authority Transfer
Node Registration
Region Assignment
Worker Coordination
Checkpoint Protocol
Used by:

Central
Runtime
Trusted Verifier
Examples:

CHECKPOINT_PROPOSE
VERIFY_REQUEST
ATTESTATION
ACCEPT
Economy Protocol
Used by:

Wallets
Escrow
Treasuries
Reward Pools
Soul Digit Ledger
Examples:

Transfers
Escrow Creation
Reward Distribution
Mint Requests
Contract Protocol
Used by:

Escort Contracts
Delivery Contracts
Crafting Orders
Construction Orders
War Contracts
Examples:

Create Contract
Accept Contract
Complete Contract
Fail Contract
Cancel Contract
Governance Protocol
Used by:

Communities
Councils
Leaders
Treasuries
Examples:

Voting
War Declaration
Community Rename
Treasury Approval
Design Principles
All protocol messages must follow:

Versioned
Deterministic
Replay Resistant
Auditable
Idempotency
Protected operations must be idempotent.

Example:

Wallet Transfer
Executing the same request twice must produce:

One Result
One State Change
not two.

Replay Protection
Critical protocol operations must include:

message_id
operation_id
checkpoint_id
authority_version
where applicable.

Duplicate operations must be rejected.

Versioning
Every protocol message should include:

{
  "protocol_version": "1.0.0"
}
This allows future upgrades without breaking compatibility.

Message Structure
Recommended message format:

{
  "protocol_version": "1.0.0",

  "message_id": "msg_001",

  "message_type": "example",

  "timestamp": "2026-01-01T00:00:00Z",

  "payload": {}
}
Message Statuses
Common statuses used across all protocol domains:

OK

PENDING

VERIFIED

COMMITTED

REJECTED

CONFLICT

CANCELLED

ERROR
Authority Awareness
Protocol operations that modify world state must verify:

Region
Epoch
Authority Version
Messages from stale authority versions must be rejected.

Checkpoint Awareness
Checkpoint-related protocols use:

Checkpoint ID
Epoch
Authority Version
to prevent replay and invalid state recovery.

Soul Digit Requirements
Economy-related protocol operations must support:

Wallet Transfers
Escrow Operations
Mint Requests
Mint Receipts
Reward Distribution
and integrate with:

Double Mint Protection
Verified Work Pipeline
Ledger Runtime
Security Requirements
Protocol implementations must:

Validate Input
Validate Authority
Validate Version
Validate Identity
Log Critical Operations
before execution.

Audit Requirements
The following operations should always be logged:

Authority Transfer
Checkpoint Commit
Mint Request
Wallet Transfer
War Declaration
Contract Completion
Logs support:

Auditing
Recovery
Forensics
Rollback Investigation
Compatibility
Protocol changes must remain compatible with:

Engine Version
Ruleset Version
Asset Version
Checkpoint Version
All compatibility validation is performed through the Compatibility Matrix.

References
Related documents:

ARCHITECTURE.md
SECURITY_SPEC.md
COMPATIBILITY_SPEC.md
MIGRATION_SPEC.md
ADR-0002-verified-checkpoint.md
ADR-0004-authority-transfer.md
ADR-0005-replay-protection.md
The Protocol Layer should remain stable and backwards compatible whenever possible, as it serves as the communication foundation of Digit World.
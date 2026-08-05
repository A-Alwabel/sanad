"""sanad_net — Sanad's real network layer (Phase 1, first light).

Real distributed inference: a GGUF model's layers are split across separate
ggml-rpc-server processes (one per node) over TCP, orchestrated by a Sanad
coordinator that assembles the pipeline and accounts non-tradeable credits.

Backend: llama.cpp's RPC mode (ggml-rpc-server + llama-cli --rpc).
Sanad's contribution is the network/fairness layer on top — see docs/ARCHITECTURE.md.
"""

__version__ = "0.1.0"

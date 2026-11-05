# Restricted Claude workflow adapter

This finite JSONL adapter uses the declared actual Claude executable, selected model and explicit private-state exclusions. It rechecks source_refs before loading/execution. A task-only no-inference check requires the actual executable's help/parser to accept the restricted flags emitted by this adapter before prepare succeeds; parser support is not falsely labelled filesystem enforcement.

Keep actual permission-denial/canary/cancellation observations separate from parser compatibility. Wrong host/session/result identities refuse. Caller reviews remain opposite-family through the discriminator; unavailable configuration, semantic FAIL, permission denial or unknown submission does not authorize fallback. No runtime provisioning, live credential copying, hooks or production session restart is performed.

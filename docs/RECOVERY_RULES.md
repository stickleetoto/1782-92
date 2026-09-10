# Recovery rules

For a mutating `apply`, a transport failure is never permission to replay the same code blindly.

1. If the bridge still answers, call `inspect({q:"summary"})` and inspect only the affected object/collection.
2. If the bridge does not answer, stop sending requests. Restore Blender connectivity first.
3. Re-run the mutation only after confirming from actual scene state that it did not take effect.
4. Prefer the latest checkpoint when Blender itself crashed.

v0.1.5 cancels jobs that time out before dispatch and cooperatively interrupts long pure-Python batches. One native Blender C call can still block beyond the cooperative deadline, so the rule above remains mandatory.

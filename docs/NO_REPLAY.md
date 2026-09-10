# No-replay invariant

A mutating apply is never automatically retried after an ambiguous transport failure. The caller must inspect actual Blender state first. This invariant is preserved in v0.1.5.

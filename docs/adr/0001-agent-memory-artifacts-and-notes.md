# Store Agent Memory Artifacts Separately From User Notes

Agent Memory uses dedicated Memory Artifacts in the database as the machine source of truth, while linked Memory Notes expose those artifacts in the existing Notes UI for user inspection and revision. This keeps consolidation, revision hashes, and read-path behavior deterministic without hiding memory from users or discarding their manual edits.

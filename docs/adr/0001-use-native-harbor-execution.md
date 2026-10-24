# Use Harbor's built-in evaluation execution

Harbor owns job configuration, task selection, trial counts, concurrency,
timeouts, retries, result storage, and supported aggregation. This repository
adds benchmark converters, task assets, scoring, and the Daydream and
subscription-judge adapters needed through supported Harbor extension points.
Keeping execution in Harbor lets multiple benchmarks share its behavior
without maintaining a separate runner or inheriting restrictions from
Daydream's current ReviewBench wrapper.
